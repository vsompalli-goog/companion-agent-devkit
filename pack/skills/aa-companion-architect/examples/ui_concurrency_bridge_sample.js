/**
 * Reference UI Concurrency Bridge for <agent-assist-companion-agent> + UiModulesConnector (FR-1.4).
 *
 * Solves the 3 client-side concurrency hazards when integrating Companion Agent UI Modules:
 *   1. Deduplicates concurrent `:analyzeContent` calls for the same `suggestionInput.answerRecord`
 *      using a 2.5-second shared Promise cache (when both `tool-action-requested` and
 *      `analyze-content-requested` fire simultaneously).
 *   2. Handles the 150ms–180ms `fetchedByConnector` fallback window so tool confirmations and
 *      workflow transitions never hang if `UiModulesConnector` drops an event.
 *   3. Mirrors updated `toolCallInfo` into both `workflowState.toolCallHistory` and
 *      `companionSuggestion.guidances[].toolCalls` so `<agent-assist-companion-agent>` clears
 *      its loading spinner cleanly.
 */

export class CompanionUiConcurrencyBridge {
  constructor({ proxyPrefix = '/uim-proxy', connectorReady = true } = {}) {
    this.proxyPrefix = proxyPrefix;
    this.connectorReady = connectorReady;
    this._recentToolConfirmCache = new Map(); // answerRecord -> { timestamp, promise }
    this._pendingToolAction = null;
    this._pendingAnalyzeNonce = null;
  }

  installFetchInterceptor() {
    if (window._companionConcurrencyIntercepted) return;
    window._companionConcurrencyIntercepted = true;

    const origFetch = window.fetch.bind(window);
    const self = this;

    window.fetch = async function (input, init = {}) {
      const url = typeof input === 'string' ? input : (input && input.url) || '';

      if (url.includes(self.proxyPrefix) && url.includes(':analyzeContent')) {
        let reqBody = {};
        try {
          if (typeof init.body === 'string' && init.body) {
            reqBody = JSON.parse(init.body);
          }
        } catch (_) {}

        if (self._pendingAnalyzeNonce) {
          self._pendingAnalyzeNonce.fetchedByConnector = true;
        }
        const toolAr = reqBody?.suggestionInput?.answerRecord || '';
        if (toolAr && self._pendingToolAction?.answerRecord === toolAr) {
          self._pendingToolAction.fetchedByConnector = true;
        }

        // 1. Deduplicate duplicate :analyzeContent calls on the same answerRecord within 2500ms
        if (toolAr) {
          const cached = self._recentToolConfirmCache.get(toolAr);
          if (cached && (Date.now() - cached.timestamp) < 2500) {
            const sharedBody = await cached.promise;
            return new Response(JSON.stringify(sharedBody), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            });
          }
        }

        const executeAnalyze = async () => {
          const res = await origFetch(input, init);
          const rawJson = await res.json().catch(() => ({}));
          return self.reconcileToolCallLocations(rawJson);
        };

        if (toolAr) {
          const sharedPromise = executeAnalyze();
          self._recentToolConfirmCache.set(toolAr, {
            timestamp: Date.now(),
            promise: sharedPromise,
          });
          try {
            const enrichedBody = await sharedPromise;
            return new Response(JSON.stringify(enrichedBody), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            });
          } catch (err) {
            self._recentToolConfirmCache.delete(toolAr);
            throw err;
          }
        }
      }

      return origFetch(input, init);
    };
  }

  attachOutboundToolActionListener(sendDirectAnalyzeRequest) {
    const addListener = window.addAgentAssistEventListener || window.addEventListener.bind(window);

    addListener('tool-action-requested', async (e) => {
      const detail = e.detail || {};
      if (detail._fromBridge) return;

      const inner = detail.payload || detail;
      const convName = detail.conversationName || inner.conversationName;
      const participantRole = inner.participantRole || 'HUMAN_AGENT';
      const req = inner.request || detail.request || {};
      const answerRecord = req.suggestionInput?.answerRecord || detail.answerRecord || '';
      const nonce = inner.nonce || `tool-confirm-${Date.now()}`;

      // Immediately dispatch analyze-content-requested so <agent-assist-companion-agent> sets store.mg loading state
      if (convName && answerRecord && typeof window.dispatchAgentAssistEvent === 'function') {
        const convId = String(convName).split('/').pop();
        window.dispatchAgentAssistEvent('analyze-content-requested', {
          detail: {
            conversationName: convName,
            conversationId: convId,
            nonce,
            participantRole,
            request: req,
            payload: { conversationId: convId, nonce, participantRole, request: req },
            _fromBridge: true,
          },
        });
      }

      const pendingTool = { nonce, answerRecord, fetchedByConnector: false };
      this._pendingToolAction = pendingTool;

      // Give UiModulesConnector 150ms to fire fetch(:analyzeContent); fall back to direct request if dropped
      if (this.connectorReady) {
        setTimeout(async () => {
          if (!pendingTool.fetchedByConnector) {
            await sendDirectAnalyzeRequest({ conversationName: convName, participantRole, nonce, request: req });
          }
        }, 150);
        return;
      }

      await sendDirectAnalyzeRequest({ conversationName: convName, participantRole, nonce, request: req });
    });
  }

  /**
   * Ensures every discovered toolCallInfo is mirrored in BOTH `workflowState.toolCallHistory`
   * AND `companionSuggestion.guidances[].toolCalls` so `<agent-assist-companion-agent>` ingests it cleanly.
   */
  reconcileToolCallLocations(analyzeResponse) {
    const cloned = JSON.parse(JSON.stringify(analyzeResponse || {}));
    for (const sr of cloned.humanAgentSuggestionResults || []) {
      const compResp = sr?.generateCompanionSuggestionsResponse;
      if (!compResp) continue;

      const wfState = compResp.workflowState;
      const guidances = compResp.companionSuggestion?.guidances || [];
      const discovered = [];

      if (wfState && Array.isArray(wfState.toolCallHistory)) {
        for (const entry of wfState.toolCallHistory) {
          const tci = entry?.toolCall?.toolCallInfo || entry?.toolCallInfo;
          if (tci) discovered.push(tci);
        }
      }
      for (const g of guidances) {
        for (const tc of g?.toolCalls || []) {
          if (tc?.toolCallInfo) discovered.push(tc.toolCallInfo);
        }
      }

      if (discovered.length > 0 && guidances.length > 0) {
        const firstGuidance = guidances[0];
        firstGuidance.toolCalls = firstGuidance.toolCalls || [];
        for (const tci of discovered) {
          const ar = tci.toolCall?.answerRecord || tci.toolCallResult?.answerRecord;
          const idx = firstGuidance.toolCalls.findIndex(
            (e) => (e?.toolCallInfo?.toolCall?.answerRecord || e?.toolCallInfo?.toolCallResult?.answerRecord) === ar
          );
          if (idx !== -1) {
            firstGuidance.toolCalls[idx] = { toolCallInfo: tci };
          } else {
            firstGuidance.toolCalls.push({ toolCallInfo: tci });
          }
        }
      }
    }
    return cloned;
  }
}
