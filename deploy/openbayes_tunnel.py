"""Forward a local H3 Studio port to a running OpenBayes SSH instance.

Set H3_SSH_PASSWORD in the environment for password authentication. The
password is never written to disk by this helper.
"""

import argparse
import os
import select
import socketserver

import paramiko


class Forwarder(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        channel = self.server.transport.open_channel(
            "direct-tcpip", (self.server.remote_host, self.server.remote_port), self.request.getpeername()
        )
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
        server.transport = client.get_transport()
        server.remote_host = args.remote_host
        server.remote_port = args.remote_port
        print(f"H3 Studio: http://127.0.0.1:{args.local_port}/extensions/h3_studio/index.html", flush=True)
        try:
            server.serve_forever()
        finally:
            client.close()


if __name__ == "__main__":
    main()
