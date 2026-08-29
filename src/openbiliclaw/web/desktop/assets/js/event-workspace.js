(() => {
  "use strict";

  const EVENT_PAGE_SIZE = 30;
  const CHANGE_PAGE_SIZE = 30;
  const EVIDENCE_PAGE_SIZE = 30;

  function text(value) {
    return String(value ?? "").trim();
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function safeExternalUrl(value) {
    try {
      const url = new URL(text(value));
      return ["http:", "https:"].includes(url.protocol) ? url.href : "";
    } catch {
      return "";
    }
  }

  function formatClock(value, { dateOnly = false } = {}) {
    const raw = text(value);
    if (!raw) return "时间待核验";
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return raw;
    const options = dateOnly
      ? { month: "2-digit", day: "2-digit" }
      : { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false };
    return new Intl.DateTimeFormat("zh-CN", options).format(parsed).replace("/", "-");
  }

  function buildEventStatement(change) {
    const previous = text(change?.previous_state) || "此前状态待核验";
    const next = text(change?.new_state) || "当前状态待核验";
    return { previous, next, plain: `${previous} → ${next}` };
  }

  function canMarkChangeRead(change) {
    return Boolean(change?.can_mark_read);
  }

  function currentStateHeadId(changes, event) {
    if (Number(event?.conflict_count || 0) > 0) return "";
    const currentState = text(event?.current_state);
    if (!currentState || !Array.isArray(changes)) return "";
    const head = changes.find((change) => (
      text(change?.lifecycle) === "active"
      && change?.is_state_applied !== false
      && text(change?.new_state) === currentState
    ));
    return text(head?.change_id);
  }

  function changeSourceScopeLabel(value) {
    return {
      multi_source: "多源佐证",
      single_source: "单源佐证",
      unknown: "来源范围待核验",
    }[text(value)] || "来源范围待核验";
  }

  function evidenceScopeLabel(item) {
    if (text(item?.evidence_completeness) === "partial_legacy") return "历史资料不完整";
    return {
      full_text: "正文级",
      summary: "摘要级",
      title_only: "标题级",
      unknown: "范围待核验",
    }[text(item?.content_scope)] || "范围待核验";
  }

  function evidenceRelationLabel(item) {
    return {
      supports_change: "支持此变化",
      contradicts_change: "与此变化冲突",
      related_no_change: "相关材料，不证明变化",
      origin: "事件起始材料",
    }[text(item?.relation_kind)] || "关联材料";
  }

  function evidenceExcerpt(item) {
    const snapshot = item?.evidence_snapshot && typeof item.evidence_snapshot === "object"
      ? item.evidence_snapshot
      : {};
    return text(
      snapshot.body_text
      || snapshot.content_text
      || snapshot.summary
      || snapshot.description
      || snapshot.text
      || snapshot.content,
    );
  }

  function classifyInboxEmpty({ summary, attention, topicGroup, query }) {
    if (text(query)) {
      return {
        icon: "ph-magnifying-glass",
        title: "没有符合搜索词的已加载事件",
        body: "搜索只缩小当前已加载的事件，不会把未加载页伪装成完整结果。",
      };
    }
    if (text(topicGroup)) {
      return {
        icon: "ph-funnel",
        title: "这个主题下还没有事件",
        body: "清除主题筛选，或先从新材料建立一个持续观察点。",
      };
    }
    if (attention === "unread" && Number(summary?.event_count || 0) > 0) {
      return {
        icon: "ph-check-circle",
        title: "未读变化已处理完",
        body: "事件档案仍保留在本机，后续出现新变化时会重新进入未读收件箱。",
      };
    }
    if (attention === "conflict" && Number(summary?.event_count || 0) > 0) {
      return {
        icon: "ph-check-circle",
        title: "当前没有待看的证据冲突",
        body: "可靠证据发生冲突时，新闻爪会冻结当前状态并单独提醒。",
      };
    }
    return {
      icon: "ph-broadcast",
      title: "还没有持续跟踪的事件",
      body: "先从一条新材料里建立后续观察点。以后新闻不会看完就消失，而会进入同一事件持续累积。",
      action: "materials",
    };
  }

  function createController(options) {
    const requestJsonStrict = options?.requestJsonStrict;
    if (typeof requestJsonStrict !== "function") {
      throw new Error("event workspace requires requestJsonStrict");
    }
    const openMaterials = typeof options.openMaterials === "function" ? options.openMaterials : () => {};
    const openTracking = typeof options.openTracking === "function" ? options.openTracking : () => {};
    const showToast = typeof options.showToast === "function" ? options.showToast : () => {};
    const onUnreadChange = typeof options.onUnreadChange === "function" ? options.onUnreadChange : () => {};
    const eventEndpoint = text(options?.endpoints?.events) || "/news/events";
    const changeEndpoint = text(options?.endpoints?.changes) || "/news/changes";
    const conflictEndpoint = text(options?.endpoints?.conflicts) || "/news/conflicts";
    const root = options.root || document;
    const byId = (id) => root.getElementById(id);

    const elements = {
      workspace: byId("eventWorkspace"),
      grid: byId("eventWorkspaceGrid"),
      topic: byId("eventTopicFilter"),
      refresh: byId("eventRefreshBtn"),
      inboxSummary: byId("eventInboxSummary"),
      inboxStatus: byId("eventInboxStatus"),
      inboxList: byId("eventInboxList"),
      inboxMore: byId("eventInboxMoreBtn"),
      pulseTitle: byId("eventPulseTitle"),
      mobileBack: byId("eventMobileBackBtn"),
      currentState: byId("eventCurrentState"),
      pulseContext: byId("eventPulseContext"),
      pulseStatus: byId("eventPulseStatus"),
      pulseTimeline: byId("eventPulseTimeline"),
      pulseMore: byId("eventPulseMoreBtn"),
      auditToggle: byId("eventAuditToggleBtn"),
      evidencePane: byId("eventEvidencePane"),
      evidenceScrim: byId("eventEvidenceScrim"),
      evidenceClose: byId("eventEvidenceCloseBtn"),
      evidenceLead: byId("eventEvidenceLead"),
      evidenceStatus: byId("eventEvidenceStatus"),
      evidenceList: byId("eventEvidenceList"),
      evidenceMore: byId("eventEvidenceMoreBtn"),
      conflictPanel: byId("eventConflictPanel"),
      conflictList: byId("eventConflictList"),
      conflictMore: byId("eventConflictMoreBtn"),
      markRead: byId("eventMarkReadBtn"),
      trackingAction: byId("eventTrackingActionBtn"),
      attentionButtons: Array.from(root.querySelectorAll("[data-event-attention]")),
    };
    if (!elements.workspace || !elements.inboxList || !elements.pulseTimeline || !elements.evidenceList) {
      throw new Error("event workspace DOM is incomplete");
    }

    const state = {
      active: false,
      bound: false,
      inboxItems: [],
      inboxSummary: {},
      globalInboxSummary: {},
      topics: [],
      inboxCursor: "",
      inboxHasMore: false,
      selectedEventId: "",
      selectedChangeId: "",
      eventDetail: null,
      changes: [],
      changeCursor: "",
      changeHasMore: false,
      changeView: "current",
      conflicts: [],
      conflictOffset: 0,
      conflictHasMore: false,
      conflictLoading: false,
      evidence: [],
      evidenceOffset: 0,
      evidenceHasMore: false,
      attention: "all",
      topicGroup: "",
      query: "",
      inboxEpoch: 0,
      eventEpoch: 0,
      evidenceEpoch: 0,
      inboxLoading: false,
      eventLoading: false,
      evidenceLoading: false,
    };
    let evidenceReturnFocus = null;
    let evidenceReturnChangeId = "";

    function selectedEvent() {
      return state.inboxItems.find((item) => text(item?.event_id) === state.selectedEventId) || state.eventDetail;
    }

    function selectedEventForView() {
      if (state.query && !filteredInboxItems().some((item) => text(item?.event_id) === state.selectedEventId)) {
        return null;
      }
      return selectedEvent();
    }

    function selectedChange() {
      return state.changes.find((item) => text(item?.change_id) === state.selectedChangeId)
        || state.conflicts.find((item) => text(item?.change_id) === state.selectedChangeId)
        || null;
    }

    function selectedIsConflict() {
      return state.conflicts.some((item) => text(item?.change_id) === state.selectedChangeId);
    }

    function filteredInboxItems() {
      const query = state.query.toLocaleLowerCase("zh-CN");
      if (!query) return state.inboxItems;
      return state.inboxItems.filter((item) => [
        item?.canonical_title,
        item?.topic_group,
        item?.current_state,
        item?.latest_change?.previous_state,
        item?.latest_change?.new_state,
      ].some((value) => text(value).toLocaleLowerCase("zh-CN").includes(query)));
    }

    function setStatus(element, message = "", tone = "") {
      if (!element) return;
      element.textContent = message;
      if (tone) element.dataset.tone = tone;
      else delete element.dataset.tone;
    }

    function statusBadge(item) {
      if (Number(item?.conflict_count || 0) > 0) {
        return '<span class="event-status-badge is-conflict"><i class="ph ph-warning-circle" aria-hidden="true"></i>有冲突</span>';
      }
      if (item?.latest_change) {
        return '<span class="event-status-badge"><i class="ph ph-check-circle" aria-hidden="true"></i>事实已核验</span>';
      }
      return '<span class="event-status-badge is-neutral"><i class="ph ph-clock" aria-hidden="true"></i>跟踪中</span>';
    }

    function renderInbox() {
      const items = filteredInboxItems();
      const selectedStillVisible = items.some((item) => text(item?.event_id) === state.selectedEventId);
      elements.inboxList.setAttribute("aria-busy", String(state.inboxLoading));
      if (!items.length) {
        const empty = classifyInboxEmpty({
          summary: state.inboxSummary,
          attention: state.attention,
          topicGroup: state.topicGroup,
          query: state.query,
        });
        elements.inboxList.innerHTML = `
          <div class="event-empty-state">
            <i class="ph ${escapeHtml(empty.icon)}" aria-hidden="true"></i>
            <strong>${escapeHtml(empty.title)}</strong>
            <p>${escapeHtml(empty.body)}</p>
            ${empty.action === "materials" ? '<div class="event-empty-actions"><button class="event-primary-button" type="button" data-event-empty-action="materials">查看新材料</button></div>' : ""}
          </div>`;
      } else {
        elements.inboxList.innerHTML = items.map((item, index) => {
          const eventId = text(item?.event_id);
          const latest = item?.latest_change;
          const changeText = Number(item?.conflict_count || 0) > 0
            ? "可靠证据冲突，当前状态已冻结"
            : latest
              ? buildEventStatement(latest).plain
              : "正在跟踪，尚未发现通过核验的变化";
          const unread = Number(item?.unread_change_count || 0) + Number(item?.unread_conflict_count || 0);
          const timeValue = latest?.effective_at || item?.last_change_at || item?.last_seen_at;
          const timeIsDateOnly = Boolean(latest?.effective_at)
            && latest?.effective_precision === "date";
          return `
            <button class="event-inbox-row" type="button" role="option"
                    data-event-id="${escapeHtml(eventId)}"
                    aria-selected="${eventId === state.selectedEventId}">
              <span class="event-inbox-index">${index + 1}</span>
              <span class="event-inbox-copy">
                <span class="event-inbox-title">${escapeHtml(item?.canonical_title || "未命名事件")}</span>
                <span class="event-inbox-change">${escapeHtml(changeText)}</span>
                <span class="event-inbox-meta"><span>${escapeHtml(item?.topic_group || "未分主题")}</span><span>${escapeHtml(formatClock(timeValue, { dateOnly: timeIsDateOnly }))}</span></span>
              </span>
              <span class="event-inbox-attention">${statusBadge(item)}${unread > 0 ? '<span class="event-unread-dot" aria-label="有未读变化"></span>' : ""}</span>
            </button>`;
        }).join("");
      }
      if (!selectedStillVisible && items.length && !state.inboxLoading) {
        void selectEvent(text(items[0]?.event_id));
      }
      elements.inboxMore.hidden = !state.inboxHasMore || Boolean(state.query);
      elements.inboxMore.disabled = state.inboxLoading;
    }

    function renderTopics() {
      if (!elements.topic) return;
      const current = state.topicGroup;
      elements.topic.innerHTML = [
        '<option value="">全部主题</option>',
        ...state.topics.map((item) => `<option value="${escapeHtml(item?.topic_group)}">${escapeHtml(item?.topic_group)}（${Number(item?.event_count || 0)}）</option>`),
      ].join("");
      elements.topic.value = current;
    }

    function syncAttentionControls() {
      elements.attentionButtons.forEach((button) => {
        const selected = text(button.dataset.eventAttention) === state.attention;
        button.setAttribute("aria-checked", String(selected));
        button.tabIndex = selected ? 0 : -1;
      });
    }

    function syncMobileDetail(open) {
      elements.grid?.classList.toggle("is-mobile-detail", Boolean(open));
    }

    function renderInboxSummary() {
      const total = Number(state.inboxSummary?.event_count || 0);
      const unread = Number(state.inboxSummary?.unread_change_count || 0);
      const conflicts = Number(state.inboxSummary?.unread_conflict_count || 0);
      elements.inboxSummary.textContent = total
        ? `${total} 个事件 · ${unread} 条未读${conflicts ? ` · ${conflicts} 条冲突` : ""}`
        : "暂无事件";
    }

    function publishGlobalUnread(summary) {
      const unread = Number(summary?.unread_change_count || 0);
      const conflicts = Number(summary?.unread_conflict_count || 0);
      onUnreadChange({
        unreadChangeCount: unread,
        unreadConflictCount: conflicts,
        unreadEventCount: Number(summary?.unread_event_count || 0),
      });
    }

    function lifecycleLabel(change) {
      if (text(change?.objectivity_status) === "disputed") return "争议版本，裁决后保留";
      if (text(change?.lifecycle) === "superseded") return "已被后续证据替代";
      if (text(change?.lifecycle) === "invalidated") return "已被后续证据判定失效";
      if (change?.is_state_applied === false) return "已核验旁支，未进入当前状态链";
      return "事实已核验";
    }

    function readStateLabel(change) {
      if (!canMarkChangeRead(change)) return "审计记录";
      return change?.is_read ? "已读" : "未读";
    }

    function renderPulse() {
      const event = selectedEventForView();
      elements.pulseTimeline.setAttribute("aria-busy", String(state.eventLoading));
      elements.pulseTitle.textContent = state.changeView === "audit" ? "完整审计历史" : "变化脉冲";
      if (!event) {
        elements.pulseContext.hidden = true;
        elements.currentState.hidden = true;
        elements.pulseTimeline.innerHTML = `
          <div class="event-empty-state"><i class="ph ph-pulse" aria-hidden="true"></i><strong>选择一个事件</strong><p>中间只展示同一事件已经核验的变化，不混入预测、情绪或系统检查记录。</p></div>`;
        elements.auditToggle.hidden = true;
        elements.pulseMore.hidden = true;
        return;
      }
      const hasConflict = Number(event?.conflict_count || 0) > 0;
      elements.pulseContext.hidden = false;
      elements.pulseContext.innerHTML = `
        <strong>${escapeHtml(event?.canonical_title || "未命名事件")}</strong>
        <span>${escapeHtml(event?.topic_group || "未分主题")} · 首次发现 ${escapeHtml(formatClock(event?.first_seen_at))} · 最近更新 ${escapeHtml(formatClock(event?.last_change_at || event?.last_seen_at))}</span>`;
      elements.currentState.hidden = false;
      elements.currentState.textContent = hasConflict
        ? "当前状态已冻结"
        : text(event?.current_state) || "当前状态待核验";
      elements.currentState.classList.toggle("is-conflict", hasConflict);
      elements.auditToggle.hidden = Number(event?.audit_change_count || 0) < 1 && !state.changes.length;
      elements.auditToggle.setAttribute("aria-pressed", String(state.changeView === "audit"));
      elements.auditToggle.textContent = state.changeView === "audit" ? "只看当前有效变化" : "显示完整审计历史";
      elements.pulseMore.hidden = !state.changeHasMore;
      elements.pulseMore.disabled = state.eventLoading;
      if (!state.changes.length) {
        const title = hasConflict ? "可靠证据互相冲突，当前状态已冻结" : "正在跟踪，尚未发现通过核验的变化";
        const body = hasConflict
          ? "冲突保留在右侧独立区域；新闻爪不会替用户猜一个事实赢家。"
          : "相关材料可以存在，但只有现实状态确实从 A 变成 B 才会进入这里。";
        const action = hasConflict
          ? '<div class="event-empty-actions"><button class="event-secondary-button" type="button" data-event-open-evidence>查看冲突证据</button></div>'
          : "";
        elements.pulseTimeline.innerHTML = `<div class="event-empty-state"><i class="ph ${hasConflict ? "ph-warning-circle" : "ph-clock"}" aria-hidden="true"></i><strong>${escapeHtml(title)}</strong><p>${escapeHtml(body)}</p>${action}</div>`;
        return;
      }
      const stateHeadId = currentStateHeadId(state.changes, event);
      elements.pulseTimeline.innerHTML = state.changes.map((change) => {
        const changeId = text(change?.change_id);
        const statement = buildEventStatement(change);
        const lifecycle = text(change?.lifecycle) || "active";
        const isBranch = lifecycle === "active" && change?.is_state_applied === false;
        const isCurrent = Boolean(stateHeadId) && changeId === stateHeadId;
        const sourceLabel = changeSourceScopeLabel(change?.source_scope);
        return `
          <article class="event-change-row ${isCurrent ? "is-current" : ""} ${isBranch ? "is-branch" : ""} ${lifecycle === "superseded" ? "is-superseded" : ""} ${lifecycle === "invalidated" ? "is-invalidated" : ""}" data-change-row="${escapeHtml(changeId)}">
            <div class="event-change-time"><time>${escapeHtml(formatClock(change?.effective_at, { dateOnly: change?.effective_precision === "date" }))}</time><span>${escapeHtml(change?.effective_precision === "date" ? "日期级" : "")}</span></div>
            <div class="event-change-axis"><span class="event-change-node"><i class="ph ${isCurrent ? "ph-check" : isBranch ? "ph-git-branch" : "ph-clock-counter-clockwise"}" aria-hidden="true"></i></span></div>
            <button class="event-change-button" type="button" data-change-id="${escapeHtml(changeId)}" aria-pressed="${changeId === state.selectedChangeId}">
              <span class="event-change-flags"><span class="event-status-badge ${lifecycle === "active" && !isBranch ? "" : "is-neutral"}">${escapeHtml(lifecycleLabel(change))}</span><span class="event-read-state ${canMarkChangeRead(change) ? (change?.is_read ? "is-read" : "is-unread") : "is-read"}">${escapeHtml(readStateLabel(change))}</span></span>
              <span class="event-change-statement"><span class="event-change-before">${escapeHtml(statement.previous)}</span><span class="event-change-arrow">→</span><span class="event-change-after">${escapeHtml(statement.next)}</span></span>
              <span class="event-change-meta"><span>现实生效 ${escapeHtml(formatClock(change?.effective_at, { dateOnly: change?.effective_precision === "date" }))}</span><span>新闻爪发现 ${escapeHtml(formatClock(change?.detected_at))}</span><span>${escapeHtml(sourceLabel)}</span><span class="event-change-open-hint"><i class="ph ph-files" aria-hidden="true"></i>查看 ${Number(change?.evidence_count || 0)} 条证据</span></span>
            </button>
          </article>`;
      }).join("");
    }

    function renderConflicts() {
      const hasConflicts = Boolean(selectedEventForView()) && state.conflicts.length > 0;
      elements.conflictPanel.hidden = !hasConflicts;
      if (!hasConflicts) {
        elements.conflictList.innerHTML = "";
        if (elements.conflictMore) elements.conflictMore.hidden = true;
        return;
      }
      elements.conflictList.innerHTML = state.conflicts.map((item) => {
        const changeId = text(item?.change_id);
        const statement = buildEventStatement(item);
        return `
          <button class="event-conflict-item event-text-button" type="button" data-conflict-id="${escapeHtml(changeId)}" aria-pressed="${changeId === state.selectedChangeId}">
            <p>${escapeHtml(statement.plain)}</p>
            <span>${item?.is_read ? "已看到" : "未读"} · ${Number(item?.evidence_count || 0)} 条证据</span>
          </button>`;
      }).join("");
      if (elements.conflictMore) {
        elements.conflictMore.hidden = !state.conflictHasMore;
        elements.conflictMore.disabled = state.conflictLoading;
        elements.conflictMore.textContent = state.conflictLoading ? "正在加载…" : "加载更多冲突版本";
      }
    }

    function renderEvidence() {
      const event = selectedEventForView();
      const change = event ? selectedChange() : null;
      elements.evidenceList.setAttribute("aria-busy", String(state.evidenceLoading));
      renderConflicts();
      if (!change) {
        elements.evidenceLead.textContent = Number(event?.conflict_count || 0) > 0
          ? "选择一条冲突事实，查看双方各自保留的证据。"
          : "选择一条变化后，这里只显示能反查到本机记录的来源。";
        elements.evidenceList.innerHTML = `<div class="event-empty-state"><i class="ph ph-list-checks" aria-hidden="true"></i><strong>等待选择变化</strong><p>来源、发布时间和证据完整度会在这里按需展开。</p></div>`;
        elements.markRead.disabled = true;
        elements.markRead.textContent = "标为已读";
        elements.evidenceMore.hidden = true;
      } else {
        const statement = buildEventStatement(change);
        const isConflict = selectedIsConflict();
        const canMarkRead = isConflict || Boolean(change?.can_mark_read);
        elements.evidenceLead.textContent = isConflict
          ? `${text(event?.canonical_title) || "此事件"}：存在相互冲突的事实版本 · 当前查看 ${Number(change?.evidence_count || 0)} 条证据`
          : `${statement.plain} · ${Number(change?.evidence_count || 0)} 条证据`;
        if (!state.evidence.length && !state.evidenceLoading) {
          elements.evidenceList.innerHTML = `<div class="event-empty-state"><i class="ph ph-warning-circle" aria-hidden="true"></i><strong>证据暂不可见</strong><p>变化记录仍保留，但本机没有可展示的证据修订；请稍后重试。</p></div>`;
        } else {
          elements.evidenceList.innerHTML = state.evidence.map((item) => {
            const href = safeExternalUrl(item?.canonical_url);
            const excerpt = evidenceExcerpt(item);
            const relation = evidenceRelationLabel(item);
            const relationTone = text(item?.relation_kind) === "contradicts_change" ? "有冲突" : relation;
            return `
              <article class="event-evidence-item">
                <div class="event-evidence-source"><strong>${escapeHtml(item?.media || item?.provider || "未注明来源")}</strong><time>${escapeHtml(formatClock(item?.published_at || item?.retrieved_at))}</time></div>
                <div class="event-evidence-title">${escapeHtml(item?.title || "未命名材料")}</div>
                ${excerpt ? `<p class="event-evidence-quote">${escapeHtml(excerpt)}</p>` : ""}
                <div class="event-evidence-meta"><span>${escapeHtml(evidenceScopeLabel(item))} · ${escapeHtml(relationTone)}</span>${href ? `<a class="event-evidence-link" href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">查看原文<i class="ph ph-arrow-square-out" aria-hidden="true"></i></a>` : ""}</div>
              </article>`;
          }).join("");
        }
        elements.markRead.disabled = !canMarkRead || Boolean(change?.is_read) || state.evidenceLoading;
        elements.markRead.textContent = change?.is_read
          ? "已读"
          : canMarkRead
            ? "标为已读"
            : "历史审计记录";
        elements.evidenceMore.hidden = !state.evidenceHasMore;
        elements.evidenceMore.disabled = state.evidenceLoading;
      }
      const trackingCount = Number(event?.active_tracking_count || 0);
      elements.trackingAction.disabled = !event;
      elements.trackingAction.textContent = trackingCount > 0 ? `查看持续跟踪（${trackingCount}）` : "从新材料建立跟踪";
    }

    function syncNarrowEvidence(open) {
      const narrow = elements.evidenceClose && getComputedStyle(elements.evidenceClose).display !== "none";
      const shouldOpen = Boolean(open && narrow);
      const wasOpen = Boolean(elements.evidencePane?.classList.contains("is-open"));
      if (shouldOpen && !wasOpen) {
        evidenceReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      }
      elements.evidencePane?.classList.toggle("is-open", shouldOpen);
      if (elements.evidenceScrim) elements.evidenceScrim.hidden = !shouldOpen;
      document.body.classList.toggle("event-evidence-sheet-open", shouldOpen);
      if (elements.evidencePane) {
        if (shouldOpen) {
          elements.evidencePane.setAttribute("role", "dialog");
          elements.evidencePane.setAttribute("aria-modal", "true");
          elements.evidencePane.setAttribute("aria-label", "事件证据链");
        } else {
          elements.evidencePane.removeAttribute("role");
          elements.evidencePane.removeAttribute("aria-modal");
          elements.evidencePane.removeAttribute("aria-label");
        }
      }
      if (shouldOpen && !wasOpen) {
        const focusClose = () => elements.evidenceClose?.focus({ preventScroll: true });
        if (typeof requestAnimationFrame === "function") requestAnimationFrame(focusClose);
        else focusClose();
      } else if (!shouldOpen && wasOpen) {
        const returnTarget = evidenceReturnChangeId
          ? state.conflicts.some((item) => text(item?.change_id) === evidenceReturnChangeId)
            ? elements.pulseTimeline.querySelector("[data-event-open-evidence]")
            : Array.from(elements.pulseTimeline.querySelectorAll("[data-change-id]"))
              .find((item) => text(item?.dataset?.changeId) === evidenceReturnChangeId)
          : evidenceReturnFocus;
        evidenceReturnFocus = null;
        evidenceReturnChangeId = "";
        if (returnTarget?.isConnected) returnTarget.focus({ preventScroll: true });
      }
    }

    function trapEvidenceFocus(event) {
      if (event.key !== "Tab" || !elements.evidencePane?.classList.contains("is-open")) return false;
      const focusable = Array.from(elements.evidencePane.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      )).filter((element) => element instanceof HTMLElement && !element.hidden);
      if (!focusable.length) {
        event.preventDefault();
        return true;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
      return true;
    }

    async function loadEvidence({ append = false } = {}) {
      const eventId = text(state.eventDetail?.event_id || state.selectedEventId);
      const changeId = state.selectedChangeId;
      if (!eventId || !changeId) {
        state.evidence = [];
        state.evidenceHasMore = false;
        renderEvidence();
        return;
      }
      const epoch = ++state.evidenceEpoch;
      state.evidenceLoading = true;
      setStatus(elements.evidenceStatus, append ? "正在加载更多证据…" : "正在读取本机证据…");
      renderEvidence();
      const offset = append ? state.evidenceOffset : 0;
      const query = new URLSearchParams({
        change_id: changeId,
        limit: String(EVIDENCE_PAGE_SIZE),
        offset: String(offset),
      });
      try {
        const payload = await requestJsonStrict(`${eventEndpoint}/${encodeURIComponent(eventId)}/evidence?${query}`, { timeoutMs: 15000, cache: "no-store" });
        if (epoch !== state.evidenceEpoch) return;
        const incoming = Array.isArray(payload?.items) ? payload.items : [];
        state.evidence = append ? [...state.evidence, ...incoming] : incoming;
        state.evidenceOffset = Number(payload?.next_offset || state.evidence.length);
        state.evidenceHasMore = Boolean(payload?.has_more);
        setStatus(elements.evidenceStatus, "");
      } catch (error) {
        if (epoch !== state.evidenceEpoch) return;
        setStatus(elements.evidenceStatus, `证据读取失败：${error?.message || "请检查本机后端"}`, "error");
      } finally {
        if (epoch === state.evidenceEpoch) {
          state.evidenceLoading = false;
          renderEvidence();
        }
      }
    }

    async function loadMoreConflicts() {
      const eventId = text(state.eventDetail?.event_id || state.selectedEventId);
      if (!eventId || !state.conflictHasMore || state.conflictLoading) return;
      state.conflictLoading = true;
      renderConflicts();
      try {
        const query = new URLSearchParams({
          limit: String(CHANGE_PAGE_SIZE),
          offset: String(state.conflictOffset),
        });
        const payload = await requestJsonStrict(
          `${eventEndpoint}/${encodeURIComponent(eventId)}/conflicts?${query}`,
          { timeoutMs: 15000, cache: "no-store" },
        );
        const incoming = Array.isArray(payload?.items) ? payload.items : [];
        const known = new Set(state.conflicts.map((item) => text(item?.change_id)));
        state.conflicts = [
          ...state.conflicts,
          ...incoming.filter((item) => !known.has(text(item?.change_id))),
        ];
        state.conflictOffset = Number(payload?.next_offset || state.conflicts.length);
        state.conflictHasMore = Boolean(payload?.has_more);
      } catch (error) {
        setStatus(elements.evidenceStatus, `冲突版本读取失败：${error?.message || "请稍后重试"}`, "error");
      } finally {
        state.conflictLoading = false;
        renderEvidence();
      }
    }

    async function selectChange(changeId, { openEvidence = true } = {}) {
      const normalized = text(changeId);
      if (!normalized) return;
      if (openEvidence) evidenceReturnChangeId = normalized;
      state.selectedChangeId = normalized;
      state.evidence = [];
      state.evidenceOffset = 0;
      state.evidenceHasMore = false;
      renderPulse();
      renderEvidence();
      if (openEvidence) syncNarrowEvidence(true);
      await loadEvidence();
    }

    async function loadSelectedEvent({ appendChanges = false, view = state.changeView } = {}) {
      const requestedEventId = state.selectedEventId;
      if (!requestedEventId) {
        state.eventDetail = null;
        state.changes = [];
        state.conflicts = [];
        state.conflictOffset = 0;
        state.conflictHasMore = false;
        state.selectedChangeId = "";
        renderPulse();
        renderEvidence();
        return;
      }
      const epoch = ++state.eventEpoch;
      const requestedView = view === "audit" ? "audit" : "current";
      state.eventLoading = true;
      setStatus(elements.pulseStatus, appendChanges ? "正在读取更早变化…" : "正在读取事件档案…");
      renderPulse();
      try {
        const changeQuery = new URLSearchParams({
          limit: String(CHANGE_PAGE_SIZE),
          view: requestedView,
        });
        if (appendChanges && state.changeCursor) changeQuery.set("cursor", state.changeCursor);
        const [detailPayload, changePayload, conflictPayload] = await Promise.all([
          requestJsonStrict(`${eventEndpoint}/${encodeURIComponent(requestedEventId)}`, { timeoutMs: 15000, cache: "no-store" }),
          requestJsonStrict(`${eventEndpoint}/${encodeURIComponent(requestedEventId)}/changes?${changeQuery}`, { timeoutMs: 15000, cache: "no-store" }),
          requestJsonStrict(`${eventEndpoint}/${encodeURIComponent(requestedEventId)}/conflicts?limit=30`, { timeoutMs: 15000, cache: "no-store" }),
        ]);
        if (epoch !== state.eventEpoch) return;
        const canonicalEvent = detailPayload?.item || changePayload?.event || null;
        if (!canonicalEvent) throw new Error("事件档案返回为空");
        state.eventDetail = {
          ...(selectedEvent() || {}),
          ...canonicalEvent,
        };
        state.selectedEventId = text(canonicalEvent.event_id) || requestedEventId;
        const incoming = Array.isArray(changePayload?.items) ? changePayload.items : [];
        state.changes = appendChanges ? [...state.changes, ...incoming] : incoming;
        if (!appendChanges) state.changeView = requestedView;
        state.changeCursor = text(changePayload?.next_cursor);
        state.changeHasMore = Boolean(changePayload?.has_more);
        state.conflicts = Array.isArray(conflictPayload?.items) ? conflictPayload.items : [];
        state.conflictOffset = Number(conflictPayload?.next_offset || state.conflicts.length);
        state.conflictHasMore = Boolean(conflictPayload?.has_more);
        const stillSelected = [...state.changes, ...state.conflicts].some((item) => text(item?.change_id) === state.selectedChangeId);
        if (!appendChanges && !stillSelected) {
          state.selectedChangeId = text(state.changes[0]?.change_id || state.conflicts[0]?.change_id);
          state.evidence = [];
          state.evidenceOffset = 0;
          state.evidenceHasMore = false;
        }
        setStatus(elements.pulseStatus, "");
        renderPulse();
        renderEvidence();
        if (state.selectedChangeId && !appendChanges) await loadEvidence();
      } catch (error) {
        if (epoch !== state.eventEpoch) return;
        const message = error?.status === 409
          ? "事件合并关系异常，暂不展示结论"
          : `事件档案读取失败：${error?.message || "请检查本机后端"}`;
        setStatus(elements.pulseStatus, message, "error");
      } finally {
        if (epoch === state.eventEpoch) {
          state.eventLoading = false;
          renderPulse();
        }
      }
    }

    async function selectEvent(eventId, { openMobileDetail = false } = {}) {
      const normalized = text(eventId);
      if (!normalized) return;
      if (normalized === state.selectedEventId && state.eventDetail) {
        if (openMobileDetail) syncMobileDetail(true);
        return;
      }
      state.selectedEventId = normalized;
      state.selectedChangeId = "";
      state.eventDetail = null;
      state.changes = [];
      state.changeCursor = "";
      state.changeHasMore = false;
      state.conflicts = [];
      state.conflictOffset = 0;
      state.conflictHasMore = false;
      state.evidence = [];
      state.evidenceOffset = 0;
      state.evidenceHasMore = false;
      renderInbox();
      renderPulse();
      renderEvidence();
      if (openMobileDetail) syncMobileDetail(true);
      await loadSelectedEvent();
    }

    async function loadGlobalSummary() {
      try {
        const summary = await requestJsonStrict(
          `${eventEndpoint}/summary?status=active`,
          { timeoutMs: 15000, cache: "no-store" },
        );
        state.globalInboxSummary = summary && typeof summary === "object" ? summary : {};
        publishGlobalUnread(state.globalInboxSummary);
      } catch {
        // Keep the last confirmed global badge; the visible workspace has its
        // own explicit error state when opened.
      }
    }

    async function loadInbox({
      append = false,
      attention = state.attention,
      topicGroup = state.topicGroup,
    } = {}) {
      const epoch = ++state.inboxEpoch;
      const requestedAttention = ["all", "unread", "conflict"].includes(attention) ? attention : "all";
      const requestedTopic = text(topicGroup);
      state.inboxLoading = true;
      setStatus(elements.inboxStatus, append ? "正在加载更多事件…" : "正在读取客观变化收件箱…");
      renderInbox();
      const query = new URLSearchParams({
        status: "active",
        attention: requestedAttention,
        limit: String(EVENT_PAGE_SIZE),
      });
      if (requestedTopic) query.set("topic_group", requestedTopic);
      if (append && state.inboxCursor) query.set("cursor", state.inboxCursor);
      try {
        const payload = await requestJsonStrict(`${eventEndpoint}?${query}`, { timeoutMs: 15000, cache: "no-store" });
        if (epoch !== state.inboxEpoch) return;
        const incoming = Array.isArray(payload?.items) ? payload.items : [];
        const previousSelection = state.selectedEventId;
        if (!append) {
          state.attention = requestedAttention;
          state.topicGroup = requestedTopic;
        }
        state.inboxItems = append ? [...state.inboxItems, ...incoming] : incoming;
        state.inboxSummary = payload?.summary && typeof payload.summary === "object" ? payload.summary : {};
        state.topics = Array.isArray(payload?.topics) ? payload.topics : [];
        state.inboxCursor = text(payload?.next_cursor);
        state.inboxHasMore = Boolean(payload?.has_more);
        if (!requestedTopic) {
          state.globalInboxSummary = state.inboxSummary;
          publishGlobalUnread(state.globalInboxSummary);
        }
        const selectionExists = state.inboxItems.some((item) => text(item?.event_id) === previousSelection);
        if (!append && !selectionExists) {
          state.selectedEventId = text(state.inboxItems[0]?.event_id);
          state.eventDetail = null;
          state.changes = [];
          state.conflicts = [];
          state.selectedChangeId = "";
        }
        renderTopics();
        syncAttentionControls();
        renderInboxSummary();
        setStatus(elements.inboxStatus, "");
        renderInbox();
        if (!append && state.selectedEventId) await loadSelectedEvent();
        if (!state.selectedEventId) {
          renderPulse();
          renderEvidence();
        }
      } catch (error) {
        if (epoch !== state.inboxEpoch) return;
        if (append && error?.status === 409) {
          state.inboxCursor = "";
          state.inboxHasMore = false;
          showToast("事件刚刚发生变化，已刷新收件箱");
          await loadInbox();
          return;
        }
        renderTopics();
        syncAttentionControls();
        setStatus(elements.inboxStatus, `事件收件箱读取失败：${error?.message || "请检查本机后端"}`, "error");
      } finally {
        if (epoch === state.inboxEpoch) {
          state.inboxLoading = false;
          renderInbox();
        }
      }
    }

    async function markSelectedRead() {
      const change = selectedChange();
      if (!change || change.is_read) return;
      const conflict = selectedIsConflict();
      if (!conflict && !change.can_mark_read) return;
      elements.markRead.disabled = true;
      const endpoint = conflict
        ? `${conflictEndpoint}/${encodeURIComponent(state.selectedChangeId)}/read`
        : `${changeEndpoint}/${encodeURIComponent(state.selectedChangeId)}/read`;
      try {
        await requestJsonStrict(endpoint, { method: "POST", timeoutMs: 15000 });
      } catch (error) {
        setStatus(elements.evidenceStatus, `标记失败：${error?.message || "请稍后重试"}`, "error");
        elements.markRead.disabled = false;
        return;
      }
      change.is_read = true;
      renderPulse();
      renderEvidence();
      showToast(conflict ? "已标记为已读；冲突事实仍保持冻结" : "已标记为已读");
      const retainedEventId = state.selectedEventId;
      await loadInbox();
      await loadGlobalSummary();
      if (retainedEventId && state.inboxItems.some((item) => text(item?.event_id) === retainedEventId)) {
        await selectEvent(retainedEventId);
      }
    }

    function rove(container, selector, current, key) {
      const items = Array.from(container.querySelectorAll(selector));
      const index = items.indexOf(current);
      if (index < 0 || !items.length) return false;
      let nextIndex = null;
      if (["ArrowDown", "ArrowRight"].includes(key)) nextIndex = (index + 1) % items.length;
      else if (["ArrowUp", "ArrowLeft"].includes(key)) nextIndex = (index - 1 + items.length) % items.length;
      else if (key === "Home") nextIndex = 0;
      else if (key === "End") nextIndex = items.length - 1;
      if (nextIndex === null) return false;
      items[nextIndex].focus();
      items[nextIndex].click();
      return true;
    }

    function bind() {
      if (state.bound) return;
      state.bound = true;
      elements.refresh?.addEventListener("click", () => void loadInbox());
      elements.inboxMore?.addEventListener("click", () => void loadInbox({ append: true }));
      elements.pulseMore?.addEventListener("click", () => void loadSelectedEvent({ appendChanges: true }));
      elements.evidenceMore?.addEventListener("click", () => void loadEvidence({ append: true }));
      elements.conflictMore?.addEventListener("click", () => void loadMoreConflicts());
      elements.markRead?.addEventListener("click", () => void markSelectedRead());
      elements.mobileBack?.addEventListener("click", () => {
        syncMobileDetail(false);
        elements.inboxList.querySelector('[aria-selected="true"]')?.focus({ preventScroll: true });
      });
      elements.trackingAction?.addEventListener("click", () => {
        const event = state.eventDetail || selectedEvent();
        if (!event) return;
        if (Number(event?.active_tracking_count || 0) > 0) openTracking(event);
        else {
          showToast("请从新材料详情里的后续观察点建立持续跟踪");
          openMaterials();
        }
      });
      elements.auditToggle?.addEventListener("click", () => {
        const nextView = state.changeView === "current" ? "audit" : "current";
        void loadSelectedEvent({ view: nextView });
      });
      elements.topic?.addEventListener("change", () => {
        void loadInbox({ topicGroup: text(elements.topic.value) });
      });
      elements.attentionButtons.forEach((button, index) => {
        button.addEventListener("click", () => {
          const next = text(button.dataset.eventAttention) || "all";
          if (next === state.attention) return;
          void loadInbox({ attention: next });
        });
        button.addEventListener("keydown", (event) => {
          if (rove(button.parentElement, "[data-event-attention]", button, event.key)) event.preventDefault();
        });
        button.tabIndex = index === 0 ? 0 : -1;
      });
      elements.inboxList.addEventListener("click", (event) => {
        const action = event.target.closest("[data-event-empty-action]");
        if (action?.dataset.eventEmptyAction === "materials") openMaterials();
        const row = event.target.closest("[data-event-id]");
        if (row) void selectEvent(row.dataset.eventId, { openMobileDetail: true });
      });
      elements.inboxList.addEventListener("keydown", (event) => {
        const row = event.target.closest("[data-event-id]");
        if (row && rove(elements.inboxList, "[data-event-id]", row, event.key)) event.preventDefault();
      });
      elements.pulseTimeline.addEventListener("click", (event) => {
        const evidenceAction = event.target.closest("[data-event-open-evidence]");
        if (evidenceAction) {
          syncNarrowEvidence(true);
          return;
        }
        const button = event.target.closest("[data-change-id]");
        if (button) void selectChange(button.dataset.changeId);
      });
      elements.pulseTimeline.addEventListener("keydown", (event) => {
        const button = event.target.closest("[data-change-id]");
        if (button && rove(elements.pulseTimeline, "[data-change-id]", button, event.key)) event.preventDefault();
      });
      elements.conflictList?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-conflict-id]");
        if (button) void selectChange(button.dataset.conflictId);
      });
      elements.evidenceClose?.addEventListener("click", () => syncNarrowEvidence(false));
      elements.evidenceScrim?.addEventListener("click", () => syncNarrowEvidence(false));
      window.addEventListener("keydown", (event) => {
        if (trapEvidenceFocus(event)) return;
        if (event.key === "Escape" && elements.evidencePane?.classList.contains("is-open")) {
          syncNarrowEvidence(false);
        }
      });
      window.addEventListener("resize", () => {
        if (elements.evidenceClose && getComputedStyle(elements.evidenceClose).display === "none") syncNarrowEvidence(false);
      });
    }

    function activate() {
      state.active = true;
      bind();
      if (!state.inboxLoading) void loadInbox();
      void loadGlobalSummary();
    }

    function deactivate() {
      state.active = false;
      syncNarrowEvidence(false);
    }

    function applySearch(value) {
      state.query = text(value);
      renderInbox();
      renderPulse();
      renderEvidence();
    }

    return {
      activate,
      applySearch,
      bind,
      deactivate,
      refresh: () => loadInbox(),
      refreshSummary: () => loadGlobalSummary(),
      snapshot: () => ({ ...state }),
    };
  }

  globalThis.NewsClawEventWorkspace = Object.freeze({
    buildEventStatement,
    changeSourceScopeLabel,
    classifyInboxEmpty,
    createController,
    currentStateHeadId,
    evidenceScopeLabel,
  });
})();
