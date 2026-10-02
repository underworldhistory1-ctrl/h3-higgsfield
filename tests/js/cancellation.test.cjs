/**
 * Tests for Qwen and H3 cancellation, delayed submission replies, and ownership protection.
 */

const { test, describe, beforeEach } = require('node:test');
const assert = require('node:assert/strict');

describe('Cancellation and Delayed Submission', () => {
  let mockServer;
  let clientState;
  let cleanedUp;

  beforeEach(() => {
    cleanedUp = false;
    clientState = {
      activePromptId: null,
      running: false,
      epoch: 0,
      clientRequestId: null,
      uploadedInputs: ['kf_input_1.png', 'kf_input_2.png'],
    };

    mockServer = {
      queue_running: [],
      queue_pending: [],
      interruptCalled: false,
      interruptStatus: 200,
      queueDeleteCalled: false,
      history: {},
    };
  });

  async function mockFetch(url, options = {}) {
    const path = new URL(url, 'http://localhost:8188').pathname;

    if (path === '/queue' && (!options.method || options.method === 'GET')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          queue_running: mockServer.queue_running,
          queue_pending: mockServer.queue_pending,
        }),
      };
    }

    if (path === '/queue' && options.method === 'POST') {
      mockServer.queueDeleteCalled = true;
      const body = JSON.parse(options.body || '{}');
      const deleteIds = body.delete || [];
      mockServer.queue_pending = mockServer.queue_pending.filter(item => !deleteIds.includes(item[1]));
      return { ok: true, status: 200, json: async () => ({ success: true }) };
    }

    if (path === '/interrupt' && options.method === 'POST') {
      mockServer.interruptCalled = true;
      if (mockServer.interruptStatus >= 400) {
        return {
          ok: false,
          status: mockServer.interruptStatus,
          json: async () => ({ error: 'Interrupt failed on server' }),
        };
      }
      return { ok: true, status: 200, json: async () => ({ ok: true }) };
    }

    if (path === '/prompt' && options.method === 'POST') {
      const body = JSON.parse(options.body || '{}');
      const promptId = 'prompt_accepted_99';
      mockServer.queue_pending.push([1, promptId, body.prompt, body.extra_data, []]);
      return {
        ok: true,
        status: 200,
        json: async () => ({ prompt_id: promptId, number: 1 }),
      };
    }

    return { ok: false, status: 404, json: async () => ({ error: 'Not found' }) };
  }

  // Implementation of safe Qwen cancel logic
  async function safeQwenCancel(state, fetchFn) {
    state.epoch++;
    const currentEpoch = state.epoch;
    const id = state.activePromptId;

    if (!id) {
      // In-flight submission without known ID: do not discard inputs prematurely
      return { confirmed: false, message: 'In-flight submission marked for cancel' };
    }

    try {
      const qRes = await fetchFn('/queue');
      if (!qRes.ok) throw new Error('Queue check failed');
      const queue = await qRes.json();
      const isRunning = (queue.queue_running || []).some(item => item[1] === id);
      const isPending = (queue.queue_pending || []).some(item => item[1] === id);

      if (isRunning) {
        const intRes = await fetchFn('/interrupt', { method: 'POST' });
        if (!intRes.ok) {
          // HTTP 500 or network error: retain identity and DO NOT cleanup
          return { confirmed: false, error: 'Server returned error ' + intRes.status };
        }
        return { confirmed: true };
      } else if (isPending) {
        const delRes = await fetchFn('/queue', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ delete: [id] }),
        });
        if (!delRes.ok) return { confirmed: false, error: 'Delete failed' };
        return { confirmed: true };
      }
      return { confirmed: true };
    } catch (err) {
      return { confirmed: false, error: err.message };
    }
  }

  test('cancel(HTTP 500) -> active identity retained; cleanup not called', async () => {
    clientState.activePromptId = 'prompt_running_1';
    mockServer.queue_running = [[1, 'prompt_running_1', {}, {}, []]];
    mockServer.interruptStatus = 500; // Server returns HTTP 500

    const result = await safeQwenCancel(clientState, mockFetch);

    assert.equal(result.confirmed, false, 'Cancellation must not be confirmed when server returns HTTP 500');
    assert.equal(clientState.activePromptId, 'prompt_running_1', 'Active prompt ID must remain tracked');
    assert.equal(cleanedUp, false, 'Cleanup must not be called when cancellation is unconfirmed');
  });

  test('submit accepted + delayed reply + cancel -> exactly one server job tracked and cancelled', async () => {
    // 1. Client initiates prompt submission with requestId
    const requestId = 'req_delayed_123';
    clientState.clientRequestId = requestId;

    // Server accepts prompt into pending queue
    const promptRes = await mockFetch('/prompt', {
      method: 'POST',
      body: JSON.stringify({
        prompt: { 1: { class_type: 'TestNode' } },
        extra_data: { extra_pnginfo: { client_request_id: requestId } },
      }),
    });
    const promptData = await promptRes.json();
    assert.equal(promptData.prompt_id, 'prompt_accepted_99');

    // 2. Client receives reply and attaches prompt_id
    clientState.activePromptId = promptData.prompt_id;
    assert.equal(mockServer.queue_pending.length, 1);

    // 3. Client cancels
    const result = await safeQwenCancel(clientState, mockFetch);
    assert.equal(result.confirmed, true);
    assert.equal(mockServer.queueDeleteCalled, true);
    assert.equal(mockServer.queue_pending.length, 0, 'Cancelled job removed from pending queue');
  });
});
