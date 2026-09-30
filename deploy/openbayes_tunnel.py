"""Forward a local H3 Studio port to a running OpenBayes SSH instance.

Set H3_SSH_PASSWORD in the environment for password authentication. The
password is never written to disk by this helper.
"""

import argparse
import os
import select
import socketserver
import threading

import paramiko


class Forwarder(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def open_remote_channel(self, peer):
        for attempt in range(2):
            with self.transport_lock:
                if self.transport is None or not self.transport.is_active():
                    self.client.close()
                    self.client.connect(self.ssh_host, port=self.ssh_port, username=self.ssh_user,
                                        password=self.ssh_password, timeout=20)
                    self.transport = self.client.get_transport()
                    self.transport.set_keepalive(30)
                try:
                    return self.transport.open_channel(
                        "direct-tcpip", (self.remote_host, self.remote_port), peer
                    )
                except (EOFError, OSError, paramiko.SSHException):
                    self.transport = None
                    if attempt:
                        raise
        return None


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            channel = self.server.open_remote_channel(self.request.getpeername())
        except (EOFError, OSError, paramiko.SSHException):
            return
        if channel is None:
            return
        try:
            while True:
                ready, _, _ = select.select([self.request, channel], [], [], 30)
                if self.request in ready:
                    data = self.request.recv(65536)
                    if not data:
                        break
                    channel.sendall(data)
                if channel in ready:
                    data = channel.recv(65536)
                    if not data:
                        break
                    self.request.sendall(data)
        finally:
            channel.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="ssh.hyper.ai")
    parser.add_argument("--ssh-port", type=int, required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--local-port", type=int, default=8766)
    parser.add_argument("--remote-host", default="127.0.0.1")
    parser.add_argument("--remote-port", type=int, default=8188)
    args = parser.parse_args()
    password = os.environ.get("H3_SSH_PASSWORD")
    if not password:
        parser.error("Set H3_SSH_PASSWORD for this process")
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(args.host, port=args.ssh_port, username=args.user, password=password, timeout=20)
    with Forwarder(("127.0.0.1", args.local_port), Handler) as server:
        server.client = client
        server.transport = client.get_transport()
        server.transport.set_keepalive(30)
        server.transport_lock = threading.Lock()
        server.ssh_host = args.host
        server.ssh_port = args.ssh_port
        server.ssh_user = args.user
        server.ssh_password = password
        server.remote_host = args.remote_host
        server.remote_port = args.remote_port
        print(f"H3 Studio: http://127.0.0.1:{args.local_port}/extensions/h3_studio/index.html", flush=True)
        try:
            server.serve_forever()
        finally:
            client.close()


if __name__ == "__main__":
    main()
