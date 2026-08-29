(() => {
    const DEFAULT_API_BASE = "http://127.0.0.1:8420/api";
    const ENDPOINTS = {
      ping: "/ping",
      health: "/health",
      qrInfo: "/qr-info",
      initStatus: "/init-status",
      startInit: "/init",
      cancelInit: "/init/cancel",
      recommendations: "/recommendations",
      contentHistory: "/content-history",
      refresh: "/recommendations/refresh",
      reshuffle: "/recommendations/reshuffle",
      append: "/recommendations/append",
      platformAvailability: "/recommendations/platform-availability",
      runtimeStatus: "/runtime-status",
      activityFeed: "/activity-feed",
      notificationPending: "/notifications/pending",
      notificationSent: "/notifications/sent",
      delightBatch: "/delight/pending-batch",
      delightRespond: "/delight/respond",
      profile: "/profile-summary",
      feedback: "/feedback",
      events: "/events",
      click: "/recommendation-click",
      chatTurns: "/chat/turns",
      dialogueContexts: "/chat/contexts",
      pendingConfirmations: "/chat/pending-confirmations",
      interestProbeRespond: "/interest-probes/respond",
      avoidanceProbeRespond: "/avoidance-probes/respond",
      sourceShareSuggestion: "/config/source-share-suggestion",
      configApplyStatus: "/config/apply-status",
      sourceCredentials: "/sources/credentials",
      configProbe: "/config/probe-service",
      configModelDiscovery: "/config/discover-models",
      embeddingRepair: "/embedding/repair",
      config: "/config",
      migrationExport: "/migration/export",
      migrationImport: "/migration/import",
      migrationPending: "/migration/pending",
      migrationStatus: "/migration/status",
      watchLater: "/watch-later",
      favorites: "/favorites",
      trackedObservations: "/news/tracked-observations",
      newsEvents: "/news/events",
      newsChanges: "/news/changes",
      newsConflicts: "/news/conflicts",
      webMonitors: "/news/monitors",
      webMonitorNotifications: "/news/monitor-notifications",
      personalMemory: "/personal-memory",
      personalMemoryHistory: "/personal-memory/history",
      profileEdit: "/profile/edit",
      profileEditState: "/profile/edit-state"
    };
    const SHARED_CHAT_SESSION = "popup";
    const CHAT_HISTORY_REFRESH_INTERVAL_MS = 2500;
    let chatHistoryRefreshTimer = null;
    let chatHistoryRefreshInFlight = false;
    let lastDialogueChatSignature = null;

    const dialogueConfirmation = globalThis.NewsClawDialogueConfirmation;
    if (!dialogueConfirmation) throw new Error("dialogue-confirmation shared helper did not load");
    const {
      activateReplyQuote,
      clearContextSelection,
      contextBarMarkup,
      contextErrorCode,
      contextErrorMessage,
      contextSelectionFromTurn,
      executeCardAction,
      executePendingConfirmationOpen,
      isCardTurn,
      isTerminalCardTurn,
      isQuestionTurn,
      normalizeContextPreview,
      readContextSelection,
      replyQuoteMarkup,
      renderMarkdown,
      renderPendingListMarkup,
      renderTurnMarkup,
      selectDialogueTurns,
      writeContextSelection,
    } = dialogueConfirmation;
    let dialogueContextSelection = readContextSelection(
      (() => {
        try { return window.localStorage; } catch { return null; }
      })(),
      "desktop-web",
    );
    let retainedChatDraft = "";
    const dialogueCardActionAbortController = new AbortController();
    const CHAT_SCROLL_BOTTOM_TOLERANCE_PX = 48;
    let hasOpenedDialogueChatPage = false;

    const state = {
      query: "",
      filter: "全部",
      mediaFilter: "全部来源",
      activeFeedback: null,
      profile: null,
      editingProfile: false,
      profileEditState: null,
      initStatus: null,
      initReason: "",
      initBusy: false,
      initSelectedSources: ["bilibili"],
      initBangumiUsername: "",
      initBangumiUsernameTouched: false,
      initBangumiUsernamePrefilled: false,
      initBangumiToken: "",
      activity: null,
      activityItems: [],
      activityCursor: "",
      activityHasMore: false,
      profileCognitionCursor: "",
      profileCognitionHasMore: false,
      delights: [],
      delightIndex: 0,
      delight: null,
      delightLoadState: "loading",
      delightLoadError: "",
      degraded: false,
      newsclawMode: null,
      config: null,
      llmDraft: null,
      llmProbeResults: new Map(),
      llmEditingInstanceId: "",
      sourceStatus: null,
      sourceCredentials: null,
      runtimeStatus: null,
      runtimeSocket: null,
      // 最近一次「成功」读到的平台可推库存快照 {total_available, by_platform}。
      // null = 尚未成功读取过（未知态）；读取失败绝不把它改写成 0。
      platformAvailability: null,
      videos: [],
      selectedRecommendationKey: "",
      recommendationDetailSheetOpen: false,
      workbenchView: "recommendations",
      homeMode: "events",
      messages: [],
      newsclawModeGeneration: 0,
      webMonitorNotificationGeneration: 0,
      webMonitorNotificationActions: new Set(),
      webMonitors: {
        items: null,
        runtime: null,
        loading: false,
        refreshRequested: false,
        refreshPromise: null,
        refreshGeneration: 0,
        error: "",
        histories: new Map(),
        historyGenerations: new Map(),
        busyIds: new Set(),
      },
      messageListSnapshot: null,
      messageListDomLocked: false,
      resolvingMessageKeys: new Set(),
      resolvedMessageResults: new Map(),
      handledProbeKeys: new Set(),
      messageScrollTop: 0,
      messageChatDomain: "",
      messageChatPrompt: "",
      messageChatScope: "probe",
      messageChatSubjectTitle: "",
      chat: [
        { role: "agent", text: "你可以直接告诉我最近想多看什么、少看什么，或者评价一条推荐为什么准/不准。" }
      ],
      pendingConfirmations: { count: 0, items: [], expanded: false },
      personalMemory: {
        dashboard: null,
        activeTab: "confirmed",
        loadState: "idle",
        error: "",
        history: [],
        historyHasMore: false,
        historyLoading: false,
        editingMemoryId: "",
        editingCandidateId: "",
        editingMemoryDraft: null,
        editingCandidateDraft: null,
        mutationBusy: false,
      }
    };
    let recommendationDetailOpener = null;
    let recommendationSyncChannel = null;
    let eventWorkspaceController = null;

    window.setInterval(() => {
      if (state.newsclawMode !== true || document.hidden) return;
      void refreshWebMonitorNotifications();
      if (desktopContentLibraryVisible && desktopContentLibraryTab === "monitoring") {
        void refreshWebMonitors({ silent: true });
      }
    }, 60000);

    const $ = (selector) => document.querySelector(selector);

    const BACK_TO_TOP_THRESHOLD = 240;

    function initBackToTop() {
      const button = $("#backToTop");
      if (!(button instanceof HTMLButtonElement)) return;

      const getScrollTargets = () => {
        const chatPage = $("#chatPage");
        const chatPageVisible = chatPage instanceof HTMLElement && !chatPage.hidden;
        return [
          chatPageVisible ? null : document.scrollingElement,
          $("#chatLog"),
          $("#messageChatLog"),
        ].filter((target, index, targets) => {
          if (!(target instanceof HTMLElement) || targets.indexOf(target) !== index) return false;
          if (target.closest("[hidden]") || window.getComputedStyle(target).display === "none") return false;
          return true;
        });
      };

      const getScrollTopTarget = () => {
        const targets = getScrollTargets();
        return targets.reduce(
          (current, target) => (target.scrollTop > (current?.scrollTop || 0) ? target : current),
          targets[0] || null,
        );
      };

      const sync = () => {
        const target = getScrollTopTarget();
        button.hidden = (target?.scrollTop || 0) < BACK_TO_TOP_THRESHOLD;
      };

      const scrollToTop = () => {
        const target = getScrollTopTarget();
        if (!target) return;
        const behavior = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ? "auto" : "smooth";
        target.scrollTo({ top: 0, behavior });
      };

      document.addEventListener("scroll", sync, true);
      window.addEventListener("resize", sync, { passive: true });
      button.addEventListener("click", scrollToTop);
      sync();
    }
    const grid = $("#videoGrid");
    const sourceFilterDefinitions = [
      { key: "cls", label: "财联社" },
      { key: "wallstreetcn", label: "华尔街见闻" },
      { key: "yicai", label: "第一财经" },
      { key: "sina", label: "新浪财经" },
      { key: "eastmoney", label: "东方财富" },
      { key: "tenjqka", label: "同花顺" },
      { key: "xinhua", label: "新华网" },
      { key: "caixin", label: "财新网" },
      { key: "cs", label: "中证网" },
      { key: "cctv", label: "央视新闻" },
      { key: "rss", label: "RSS" },
    ];
    const sourceFilterOrder = sourceFilterDefinitions.map((source) => source.label);
    // 首次成功读到库存快照之前是"未知"，不能把还没读到伪装成 0。
    const PLATFORM_COUNT_UNKNOWN_TEXT = "—";
    const PLATFORM_COUNT_UNKNOWN_LABEL = "库存待读取";
    const platformLabel = { cls: "财联社", wallstreetcn: "华尔街见闻", yicai: "第一财经", sina: "新浪财经", eastmoney: "东方财富", tenjqka: "同花顺", xinhua: "新华网", caixin: "财新网", cs: "中证网", cctv: "央视新闻", tushare: "Tushare", rss: "RSS" };
    const platformAliases = { cls: "cls", "财联社": "cls", wallstreetcn: "wallstreetcn", "华尔街见闻": "wallstreetcn", yicai: "yicai", "第一财经": "yicai", sina: "sina", "新浪财经": "sina", eastmoney: "eastmoney", "东方财富": "eastmoney", tenjqka: "tenjqka", "同花顺": "tenjqka", xinhua: "xinhua", "新华网": "xinhua", caixin: "caixin", "财新网": "caixin", cs: "cs", "中证网": "cs", cctv: "cctv", "央视新闻": "cctv", tushare: "tushare", rss: "rss" };
    const textCardContentTypes = new Set(["tweet", "thread", "answer", "article", "question", "post", "comment"]);
    const DISPLAY_TAG_TYPES = new Set(["topic", "story", "temporal", "evidence"]);
    const DISPLAY_TAG_TYPE_LABELS = { topic: "主题", story: "故事", temporal: "时效", evidence: "证据" };
    const ALL_MEDIA_FILTER = "全部来源";
    // v0.3.118+: bilibili is selectable like every other source — default
    // checked (recommended) but no longer forced. At least one source must
    // stay checked to start.
    //
    // WHICH sources exist comes from the shared roster (/shared/source-status.js,
    // loaded before this script), the same list the setup wizard and the side
    // panel build their pickers from — a hardcoded copy here is what let the
    // three surfaces drift. Labels come from the shared module too, with a local
    // fallback map so an unrecognised key still renders. defaultChecked stays
    // local first-run policy (the backend mirrors it in providers._ENABLED_BY_DEFAULT).
    const INIT_SOURCE_LABEL_FALLBACK = {
      tushare_news: "Tushare 快讯", tushare_major_news: "Tushare 长篇新闻",
      tushare_cctv_news: "央视新闻联播", official_rss: "官方 RSS", custom_rss: "自定义 RSS"
    };
    const INIT_SOURCE_DEFAULT_CHECKED = new Set(["tushare_news", "tushare_major_news", "tushare_cctv_news", "official_rss"]);
    const _initSourceStatus = globalThis.NewsClawSourceStatus || null;
    const INIT_SOURCE_KEYS = (_initSourceStatus?.INIT_SOURCE_KEYS || _initSourceStatus?.SOURCE_KEYS)
      ? [...(_initSourceStatus.INIT_SOURCE_KEYS || _initSourceStatus.SOURCE_KEYS)]
      : Object.keys(INIT_SOURCE_LABEL_FALLBACK);
    const INIT_SOURCE_OPTIONS = INIT_SOURCE_KEYS.map((key) => ({
      key,
      label: _initSourceStatus?.sourceLabel?.(key) || INIT_SOURCE_LABEL_FALLBACK[key] || key,
      ...(INIT_SOURCE_DEFAULT_CHECKED.has(key) ? { defaultChecked: true } : {})
    }));
    const INIT_SOURCE_LOGIN_HINT = "勾选要启用的新闻源（至少一个）。NewsClaw 只调用已配置的 Tushare 接口与 RSS，不读取浏览器 Cookie，也不抓取网页。";
    const INIT_REASON_TEXT = {
      unsupported_runtime: "当前运行环境不支持在网页里启动初始化。请回到运行新闻爪的电脑完成首次设置。",
      already_running: "初始化正在进行中。",
      bilibili_not_logged_in: "新闻源尚未验证，请前往首次设置逐项检查。",
      llm_not_ready: "AI 服务还没配好或当前不可用。",
      embedding_not_ready: "向量模型还没就绪，请等待 bge-m3 下载完成或修复 Ollama 后重试。",
      already_initialized: "已经初始化过了；如需重建，请到设置页。",
      local_only: "只能在本机发起初始化。",
      no_sources_selected: "至少勾选一个数据来源。",
      no_profile_signal_sources: "至少选择一个可用新闻源，并填写一个关注方向。",
      invalid_bangumi_access_token: "新闻源凭据被拒绝（缺失、错误、过期或无接口权限）。",
      bangumi_token_check_failed: "校验新闻源凭据时连接失败，请稍后重试。",
      analyze_failed: "偏好分析未完成。",
      profile_failed: "画像生成未完成。",
      discovery_timeout: "画像已生成，但首轮内容池整理超时。",
      discovery_partial: "画像已生成，但首轮内容池本次未完成。",
      douyin_degraded: "抖音已采数据已用于画像，但至少一个账号范围的分页未完整完成。",
      internal_error: "初始化过程中出错了，请稍后重试。",
      interrupted: "上次初始化被打断（后端重启），可重试。",
      cancelled: "初始化已取消。",
      collection_timeout: "数据采集达到总等待上限，已停止继续等待平台或扩展。",
      none: ""
    };
    const INIT_STATUS_POLL_MS = Number(window.__OBC_TEST_INIT_POLL_MS) || 3000;
    const INIT_STATUS_START_POLL_MS = Number(window.__OBC_TEST_INIT_START_POLL_MS) || 1200;
    const INIT_STATUS_WATCHDOG_MS = Number(window.__OBC_TEST_INIT_WATCHDOG_MS) || 15000;
    const CHAT_PLACEHOLDERS = [
      "说说你最近怎么想——你是什么样的人、喜欢什么、讨厌什么，都可以直接说。",
      "比如：我喜欢慢慢讲清楚的长新闻，讨厌标题党和故意搞悬念的。",
      "比如：最近老点开国际新闻和商业分析，想知道自己到底在找什么。",
      "比如：我经常刷到一半就退出，好像注意力很难集中。",
      "比如：我偏爱小众冷门内容，热门排行榜上的反而不太想看。",
      "比如：这阵子心情一般，老看一些治愈系的东西。",
      "比如：我在学一门新技能，想看看有没有靠谱教程。"
    ];
    let chatPlaceholderIndex = 0;
    let chatPlaceholderTimer = null;
    let activityRailHeightFrame = 0;
    let backendHydrationTimer = null;
    let backendHydrationInFlight = false;
    let backendHydrationPending = false;
    let initPollTimer = null;
    let initRefreshInFlight = false;
    let initRefreshPending = false;
    let activityPageRefreshTimer = null;
    let activityPageRefreshInFlight = false;
    let activityPageRefreshPending = false;
    const DESKTOP_RECOVERY_DELAYS_MS = [1000, 2000, 4000, 8000];
    let desktopRecommendationLoadState = "loading";
    let desktopRuntimeLoadState = "idle";
    let desktopRecommendationRecoveryAttempt = 0;
    let desktopRuntimeRecoveryAttempt = 0;
    let desktopRecommendationRecoveryTimer = null;
    let desktopRuntimeRecoveryTimer = null;
    let desktopRecommendationRecoveryInFlight = false;
    let desktopRuntimeRecoveryInFlight = false;
    let desktopRuntimeGeneration = 0;
    let degradedRecoveryPresented = false;
    const DESKTOP_RESUME_HYDRATE_TTL_MS = 15000;
    let desktopBackendSessionInFlight = false;
    let desktopLastHydratedAt = 0;
    let desktopRuntimeReconnectTimer = null;

    function debounceAsync(fn, delayMs = 1000) {
      let timer = null;
      let inFlight = false;
      let pending = false;
      const run = async () => {
        if (inFlight) { pending = true; return; }
        inFlight = true;
        try { await fn(); } finally {
          inFlight = false;
          if (pending) { pending = false; timer = window.setTimeout(run, 0); }
        }
      };
      return () => {
        if (timer !== null) window.clearTimeout(timer);
        timer = window.setTimeout(() => { timer = null; run(); }, delayMs);
      };
    }

    const scheduleDelightQueueRefresh = debounceAsync(() => fetchDelightQueue(), 1000);

    // 库存变化事件可能成串到达（补货一轮会连发多条），去抖 + 单飞（合并 pending
    // 调用）避免把只读快照接口打成风暴。debounceAsync 已实现这两点。
    const schedulePlatformAvailabilityRefresh = debounceAsync(() => refreshPlatformAvailability(), 600);
    const scheduleDialogueConfirmationRefresh = debounceAsync(
      () => refreshDialogueConfirmationSurface(),
      300
    );
    const scheduleDesktopPendingConfirmationRefresh = debounceAsync(
      () => refreshDesktopPendingConfirmations().catch(() => {}),
      300
    );

    let platformAvailabilityRetryAttempt = 0;
    let platformAvailabilityRetryTimer = null;

    function normalizePlatformAvailability(payload) {
      const total = Number(payload?.total_available);
      if (!Number.isFinite(total)) return null;
      const byPlatform = {};
      const raw = payload?.by_platform;
      if (raw && typeof raw === "object" && !Array.isArray(raw)) {
        for (const [key, value] of Object.entries(raw)) {
          const slug = canonicalPlatformSlug(key);
          const count = Number(value);
          if (!slug || !Number.isFinite(count) || count <= 0) continue;
          byPlatform[slug] = (byPlatform[slug] || 0) + Math.trunc(count);
        }
      }
      return { total_available: Math.max(0, Math.trunc(total)), by_platform: byPlatform };
    }

    // 首次读取失败后的有界恢复；成功过一次就不再重试（后续由库存事件驱动）。
    function schedulePlatformAvailabilityRetry() {
      if (document.hidden) return;
      if (state.platformAvailability) return;
      if (platformAvailabilityRetryTimer !== null) return;
      if (platformAvailabilityRetryAttempt >= DESKTOP_RECOVERY_DELAYS_MS.length) return;
      const delayMs = DESKTOP_RECOVERY_DELAYS_MS[platformAvailabilityRetryAttempt];
      platformAvailabilityRetryAttempt += 1;
      platformAvailabilityRetryTimer = window.setTimeout(() => {
        platformAvailabilityRetryTimer = null;
        void refreshPlatformAvailability();
      }, delayMs);
    }

    async function refreshPlatformAvailability() {
      try {
        const snapshot = normalizePlatformAvailability(
          await requestJsonStrict(ENDPOINTS.platformAvailability, { timeoutMs: 15000, cache: "no-store" })
        );
        if (!snapshot) throw new Error("platform availability unavailable");
        // 只有成功 snapshot 才覆盖旧值。
        state.platformAvailability = snapshot;
        platformAvailabilityRetryAttempt = 0;
        // 库存更新只允许重绘 Tab / 空态与自动续页 gate；已经 append 的推荐卡片
        // 不重建、不覆盖（renderVideos 只在当前就是空态或 Tab 被迫回退时才跑）。
        const previousFilter = state.filter;
        renderFilters();
        if (state.filter !== previousFilter || grid?.querySelector(".empty-state")) renderVideos();
        maybeAutoLoadAfterPoolRefill();
      } catch {
        // 读取失败保留上一次成功的数字；"失败即全零" 是明确禁止的。
        schedulePlatformAvailabilityRetry();
      }
    }

    let configSnapshotRetryAttempt = 0;
    let configSnapshotRetryTimer = null;
    let configSnapshotRecoveryInFlight = false;

    // 配置快照只在水合时读一次；失败被 requestJson 静默吞掉会让筛选行永久缺失
    // 已启用但零库存的平台（库存快照不会包含这类平台），设置页也拿不到默认值。
    // 与平台库存一样做有界重试，成功后由后续保存 / 刷新自然接管。
    function scheduleConfigSnapshotRetry() {
      if (document.hidden) return;
      if (state.config) return;
      if (configSnapshotRetryTimer !== null) return;
      if (configSnapshotRetryAttempt >= DESKTOP_RECOVERY_DELAYS_MS.length) return;
      const delayMs = DESKTOP_RECOVERY_DELAYS_MS[configSnapshotRetryAttempt];
      configSnapshotRetryAttempt += 1;
      configSnapshotRetryTimer = window.setTimeout(() => {
        configSnapshotRetryTimer = null;
        void loadConfigSnapshot();
      }, delayMs);
    }

    async function loadConfigSnapshot() {
      if (configSnapshotRecoveryInFlight) return;
      configSnapshotRecoveryInFlight = true;
      try {
        const snapshot = await requestJson(ENDPOINTS.config);
        if (!snapshot) {
          scheduleConfigSnapshotRetry();
          return;
        }
        configSnapshotRetryAttempt = 0;
        applyConfigSnapshot(snapshot);
      } finally {
        configSnapshotRecoveryInFlight = false;
      }
    }

    async function runBackendHydration() {
      if (document.hidden) {
        backendHydrationPending = true;
        return;
      }
      if (settingsDirtyFields.size > 0 || settingsFormHasActiveEditor()) {
        // 事件到真正执行再水合之间有 1 秒防抖；这段时间里用户可能已经开始下一轮编辑。
        // 执行前再次检查，不能让过期快照清空刚出现的本地草稿。
        return;
      }
      if (backendHydrationInFlight) {
        backendHydrationPending = true;
        return;
      }
      backendHydrationInFlight = true;
      try {
        await hydrateFromBackend();
      } finally {
        backendHydrationInFlight = false;
        if (backendHydrationPending) {
          backendHydrationPending = false;
          backendHydrationTimer = window.setTimeout(() => {
            backendHydrationTimer = null;
            void runBackendHydration();
          }, 0);
        }
      }
    }

    function scheduleBackendHydration() {
      if (document.hidden) {
        backendHydrationPending = true;
        return;
      }
      if (backendHydrationTimer !== null) window.clearTimeout(backendHydrationTimer);
      backendHydrationTimer = window.setTimeout(() => {
        backendHydrationTimer = null;
        void runBackendHydration();
      }, 1000);
    }

    async function readRecommendationSnapshot() {
      const payload = await requestJsonStrict(ENDPOINTS.recommendations, { timeoutMs: 15000 });
      return Array.isArray(payload) ? payload : asArray(payload?.items);
    }

    async function readRuntimeStatusSnapshot() {
      const payload = await requestJsonStrict(ENDPOINTS.runtimeStatus, { timeoutMs: 15000, cache: "no-store" });
      return payload?.status || payload;
    }

    function clearDesktopRecommendationRecovery(nextState) {
      if (desktopRecommendationRecoveryTimer !== null) {
        window.clearTimeout(desktopRecommendationRecoveryTimer);
        desktopRecommendationRecoveryTimer = null;
      }
      desktopRecommendationRecoveryAttempt = 0;
      desktopRecommendationLoadState = nextState;
    }

    function clearDesktopRuntimeRecovery(nextState = "ready") {
      if (desktopRuntimeRecoveryTimer !== null) {
        window.clearTimeout(desktopRuntimeRecoveryTimer);
        desktopRuntimeRecoveryTimer = null;
      }
      desktopRuntimeRecoveryAttempt = 0;
      desktopRuntimeLoadState = nextState;
      const poolAvailable = $("#poolAvailable");
      if (poolAvailable) {
        poolAvailable.onclick = null;
        poolAvailable.onkeydown = null;
        poolAvailable.removeAttribute("role");
        poolAvailable.removeAttribute("tabindex");
        poolAvailable.removeAttribute("aria-label");
      }
    }

    function applyDesktopRecommendationSnapshot(items, { replace = false } = {}) {
      const normalized = normalizeRecommendationList(items);
      if (normalized.length > 0) {
        desktopRecommendationLoadState = "ready";
      } else {
        desktopRecommendationLoadState = "empty-success";
      }
      clearDesktopRecommendationRecovery(desktopRecommendationLoadState);
      if (!replace && state.videos.length > 0) return;
      state.videos = normalized;
    }

    function applyDesktopRuntimeSnapshot(payload, requestGeneration) {
      if (requestGeneration !== desktopRuntimeGeneration) return false;
      if (!payload) throw new Error("runtime status unavailable");
      desktopRuntimeGeneration += 1;
      clearDesktopRuntimeRecovery();
      applyRuntimeStatus(payload);
      return true;
    }

    function renderDesktopRuntimeFailure() {
      if (desktopRuntimeLoadState !== "failed" && desktopRuntimeLoadState !== "failed-exhausted") return;
      const exhausted = desktopRuntimeLoadState === "failed-exhausted";
      setSideHealthState(state.runtimeStatus ? "degraded" : "offline", state.runtimeStatus ? "状态过期" : "离线");
      if (!state.runtimeStatus) {
        $("#metricPool").textContent = "—";
        $("#poolAvailable").textContent = exhausted ? "同步失败，点击重试" : "同步失败，正在重试";
        $("#runtimeSummary").textContent = "库存状态读取失败；这不代表候选池真的为空。";
      } else {
        $("#runtimeSummary").textContent = exhausted
          ? "库存状态同步失败；当前显示的是上次成功读取的库存，点击库存数可重试。"
          : "库存状态同步失败，正在重试；当前显示的是上次成功读取的库存。";
      }
      $("#poolRefreshState").textContent = exhausted ? "同步失败，点击库存重试" : "状态重试中";
      if (exhausted) {
        const poolAvailable = $("#poolAvailable");
        poolAvailable.setAttribute("role", "button");
        poolAvailable.setAttribute("tabindex", "0");
        poolAvailable.setAttribute("aria-label", "库存状态同步失败，重新加载");
        poolAvailable.onclick = restartDesktopFailedRecoveries;
        poolAvailable.onkeydown = (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            restartDesktopFailedRecoveries();
          }
        };
      }
    }

    function scheduleDesktopRecommendationRecovery() {
      if (document.hidden) return;
      if (state.videos.length > 0) {
        clearDesktopRecommendationRecovery("ready");
        return;
      }
      if (desktopRecommendationLoadState !== "failed") return;
      if (desktopRecommendationRecoveryInFlight || desktopRecommendationRecoveryTimer !== null) return;
      if (desktopRecommendationRecoveryAttempt >= DESKTOP_RECOVERY_DELAYS_MS.length) {
        desktopRecommendationLoadState = "failed-exhausted";
        renderVideos();
        return;
      }
      const delayMs = DESKTOP_RECOVERY_DELAYS_MS[desktopRecommendationRecoveryAttempt];
      desktopRecommendationRecoveryTimer = window.setTimeout(() => {
        desktopRecommendationRecoveryTimer = null;
        desktopRecommendationRecoveryAttempt += 1;
        void runDesktopRecommendationRecovery();
      }, delayMs);
    }

    async function runDesktopRecommendationRecovery() {
      if (state.videos.length > 0) {
        clearDesktopRecommendationRecovery("ready");
        return;
      }
      if (desktopRecommendationLoadState !== "failed" || desktopRecommendationRecoveryInFlight) return;
      desktopRecommendationRecoveryInFlight = true;
      try {
        applyDesktopRecommendationSnapshot(await readRecommendationSnapshot());
      } catch {
        desktopRecommendationLoadState = "failed";
      } finally {
        desktopRecommendationRecoveryInFlight = false;
        renderVideos();
        scheduleDesktopRecommendationRecovery();
      }
    }

    function scheduleDesktopRuntimeRecovery() {
      if (document.hidden) return;
      if (desktopRuntimeLoadState !== "failed") return;
      if (desktopRuntimeRecoveryInFlight || desktopRuntimeRecoveryTimer !== null) return;
      if (desktopRuntimeRecoveryAttempt >= DESKTOP_RECOVERY_DELAYS_MS.length) {
        desktopRuntimeLoadState = "failed-exhausted";
        renderDesktopRuntimeFailure();
        return;
      }
      const delayMs = DESKTOP_RECOVERY_DELAYS_MS[desktopRuntimeRecoveryAttempt];
      desktopRuntimeRecoveryTimer = window.setTimeout(() => {
        desktopRuntimeRecoveryTimer = null;
        desktopRuntimeRecoveryAttempt += 1;
        void runDesktopRuntimeRecovery();
      }, delayMs);
    }

    async function runDesktopRuntimeRecovery() {
      if (desktopRuntimeLoadState !== "failed" || desktopRuntimeRecoveryInFlight) return;
      desktopRuntimeRecoveryInFlight = true;
      const requestGeneration = desktopRuntimeGeneration;
      try {
        const applied = applyDesktopRuntimeSnapshot(
          await readRuntimeStatusSnapshot(),
          requestGeneration
        );
        // Initial recommendation and runtime reads recover independently. If
        // recommendations recover first, the guided-init gate remains in the
        // grid until the runtime snapshot proves the first pool is ready.
        // Refresh only that gate; do not rebuild healthy, interactive cards.
        if (applied && grid.querySelector(".init-onboarding")) renderVideos();
      } catch {
        if (requestGeneration !== desktopRuntimeGeneration) return;
        desktopRuntimeLoadState = "failed";
      } finally {
        desktopRuntimeRecoveryInFlight = false;
        scheduleDesktopRuntimeRecovery();
        renderDesktopRuntimeFailure();
      }
    }

    function restartDesktopFailedRecoveries() {
      let recommendationRestarted = false;
      let runtimeRestarted = false;
      if (
        state.videos.length === 0 &&
        (desktopRecommendationLoadState === "failed" || desktopRecommendationLoadState === "failed-exhausted")
      ) {
        if (desktopRecommendationRecoveryTimer !== null) window.clearTimeout(desktopRecommendationRecoveryTimer);
        desktopRecommendationRecoveryTimer = null;
        desktopRecommendationRecoveryAttempt = 0;
        desktopRecommendationLoadState = "failed";
        scheduleDesktopRecommendationRecovery();
        recommendationRestarted = true;
      }
      if (desktopRuntimeLoadState === "failed" || desktopRuntimeLoadState === "failed-exhausted") {
        if (desktopRuntimeRecoveryTimer !== null) window.clearTimeout(desktopRuntimeRecoveryTimer);
        desktopRuntimeRecoveryTimer = null;
        desktopRuntimeRecoveryAttempt = 0;
        desktopRuntimeLoadState = "failed";
        scheduleDesktopRuntimeRecovery();
        runtimeRestarted = true;
      }
      if (recommendationRestarted) renderVideos();
      if (runtimeRestarted) renderDesktopRuntimeFailure();
      // 用户重试也重开一次库存快照读取（首次失败后的 Tab 计数仍是未知态）。
      if (!state.platformAvailability) {
        if (platformAvailabilityRetryTimer !== null) window.clearTimeout(platformAvailabilityRetryTimer);
        platformAvailabilityRetryTimer = null;
        platformAvailabilityRetryAttempt = 0;
        schedulePlatformAvailabilityRefresh();
      }
    }

    async function runActivityPageRefresh() {
      if (activityPageRefreshInFlight) {
        activityPageRefreshPending = true;
        return;
      }
      activityPageRefreshInFlight = true;
      try {
        await loadActivityPage({ reset: true });
      } finally {
        activityPageRefreshInFlight = false;
        if (activityPageRefreshPending) {
          activityPageRefreshPending = false;
          activityPageRefreshTimer = window.setTimeout(() => {
            activityPageRefreshTimer = null;
            void runActivityPageRefresh();
          }, 0);
        }
      }
    }

    function scheduleActivityPageRefresh() {
      if (document.hidden) {
        activityPageRefreshPending = true;
        return;
      }
      if (activityPageRefreshTimer !== null) window.clearTimeout(activityPageRefreshTimer);
      activityPageRefreshTimer = window.setTimeout(() => {
        activityPageRefreshTimer = null;
        void runActivityPageRefresh();
      }, 1000);
    }

    function syncActivityRailHeight() {
      const rail = document.querySelector('[data-od-id="activity-rail"]');
      const delight = document.getElementById("delightBanner");
      if (!rail || !delight || !window.matchMedia("(min-width: 1181px)").matches) {
        rail?.style.removeProperty("--activity-rail-max-height");
        return;
      }
      const height = Math.ceil(delight.getBoundingClientRect().height);
      if (height > 0) rail.style.setProperty("--activity-rail-max-height", `${height}px`);
    }

    function scheduleActivityRailHeightSync() {
      if (activityRailHeightFrame) cancelAnimationFrame(activityRailHeightFrame);
      activityRailHeightFrame = requestAnimationFrame(() => {
        activityRailHeightFrame = 0;
        syncActivityRailHeight();
      });
    }

    function showFatal(error, context = "页面启动") {
      const message = error?.message || String(error || "未知错误");
      const banner = $("#fatalBanner");
      if (banner) {
        banner.textContent = `${context}出现问题：${message}`;
        banner.classList.add("is-open");
      }
      const status = $("#statusLabel");
      if (status) status.textContent = `${context}异常`;
      const summary = $("#runtimeSummary");
      if (summary) summary.textContent = message;
      console.error(context, error);
    }

    const FOREIGN_SCRIPT_URL_RE = /\b(?:chrome-extension|moz-extension|safari-web-extension|safari-extension|user-script|greasemonkey-script):/i;

    function isForeignScriptError(event) {
      const filename = event?.filename || "";
      // 跨域脚本的错误会被浏览器脱敏成空 filename + "Script error."，本站资源不会
      if (!filename) return true;
      try {
        return new URL(filename, window.location.href).origin !== window.location.origin;
      } catch {
        return true;
      }
    }

    function isForeignRejection(reason) {
      const stack = typeof reason?.stack === "string" ? reason.stack : "";
      return FOREIGN_SCRIPT_URL_RE.test(stack) && !stack.includes(window.location.origin);
    }

    window.addEventListener("error", (event) => {
      if (isForeignScriptError(event)) {
        console.warn("已忽略非本站脚本错误（通常来自浏览器扩展/油猴脚本）:", event.filename || "(跨域)", event.message);
        return;
      }
      showFatal(event.error || event.message, "页面脚本");
    });
    window.addEventListener("unhandledrejection", (event) => {
      if (isForeignRejection(event.reason)) {
        console.warn("已忽略非本站脚本 Promise 错误（通常来自浏览器扩展/油猴脚本）:", event.reason);
        return;
      }
      showFatal(event.reason, "异步加载");
    });

    function storageGet(key) {
      try { return window.localStorage?.getItem(key) || ""; } catch { return ""; }
    }

    function storageSet(key, value) {
      try { window.localStorage?.setItem(key, value); } catch {}
    }

    const NEWS_DELIGHT_PLATFORM = "news";
    const DELIGHT_STORAGE_SCOPE_RE = /(?:active[_-]?delights?|delight(?:[_-]?(?:queue|cache|state))?|(?:queue|cache)[_-]?delight)/i;
    const DELIGHT_STORAGE_SETTING_RE = /(?:limit|setting|preference|config)/i;

    function isNewsDelightItem(item) {
      const candidate = item?.data && typeof item.data === "object" ? item.data : item;
      return String(candidate?.source_platform ?? "").trim().toLowerCase() === NEWS_DELIGHT_PLATFORM;
    }

    function sanitizeLegacyDelightPayload(value, { delightScope = false } = {}) {
      if (Array.isArray(value)) {
        if (!delightScope) return { value, changed: false };
        const filtered = value.filter(isNewsDelightItem);
        return { value: filtered, changed: filtered.length !== value.length };
      }
      if (!value || typeof value !== "object") return { value, changed: false };

      if (delightScope && ("source_platform" in value || "bvid" in value || "article_id" in value)) {
        return isNewsDelightItem(value)
          ? { value, changed: false }
          : { value: null, changed: true };
      }

      let changed = false;
      const next = { ...value };
      for (const [key, child] of Object.entries(value)) {
        const childScope = delightScope
          ? /^(?:items|queue|candidates|current|active)$/i.test(key) || DELIGHT_STORAGE_SCOPE_RE.test(key)
          : DELIGHT_STORAGE_SCOPE_RE.test(key);
        if (!childScope) continue;
        const sanitized = sanitizeLegacyDelightPayload(child, { delightScope: true });
        if (!sanitized.changed) continue;
        next[key] = sanitized.value;
        changed = true;
      }
      return { value: next, changed };
    }

    function purgeLegacyDelightLocalStorage() {
      let storage;
      try { storage = window.localStorage; } catch { return; }
      if (!storage) return;
      for (let index = storage.length - 1; index >= 0; index -= 1) {
        const key = storage.key(index);
        if (!key || DELIGHT_STORAGE_SETTING_RE.test(key)) continue;
        let parsed;
        try { parsed = JSON.parse(storage.getItem(key) || ""); } catch { continue; }
        const keyOwnsDelights = DELIGHT_STORAGE_SCOPE_RE.test(key);
        const sanitized = sanitizeLegacyDelightPayload(parsed, { delightScope: keyOwnsDelights });
        if (!sanitized.changed) continue;
        try {
          if (sanitized.value === null || (Array.isArray(sanitized.value) && sanitized.value.length === 0)) {
            storage.removeItem(key);
          } else {
            storage.setItem(key, JSON.stringify(sanitized.value));
          }
        } catch {}
      }
    }

    async function purgeLegacyDelightIndexedDb() {
      const dbApi = globalThis.indexedDB;
      if (!dbApi || typeof dbApi.databases !== "function") return;
      let databases;
      try { databases = await dbApi.databases(); } catch { return; }
      const legacyDbNames = databases
        .map((entry) => String(entry?.name || ""))
        .filter((name) => name && /(?:openbiliclaw|newsclaw|\bobc\b)/i.test(name));

      await Promise.allSettled(legacyDbNames.map((name) => new Promise((resolve) => {
        const request = dbApi.open(name);
        request.onerror = () => resolve();
        request.onblocked = () => resolve();
        request.onsuccess = () => {
          const database = request.result;
          const storeNames = Array.from(database.objectStoreNames).filter(
            (storeName) => DELIGHT_STORAGE_SCOPE_RE.test(storeName) || /(?:state|cache)/i.test(storeName),
          );
          if (!storeNames.length) {
            database.close();
            resolve();
            return;
          }
          let transaction;
          try { transaction = database.transaction(storeNames, "readwrite"); }
          catch {
            database.close();
            resolve();
            return;
          }
          transaction.oncomplete = transaction.onerror = transaction.onabort = () => {
            database.close();
            resolve();
          };
          for (const storeName of storeNames) {
            const delightScope = DELIGHT_STORAGE_SCOPE_RE.test(storeName);
            const cursorRequest = transaction.objectStore(storeName).openCursor();
            cursorRequest.onsuccess = () => {
              const cursor = cursorRequest.result;
              if (!cursor) return;
              const sanitized = sanitizeLegacyDelightPayload(cursor.value, { delightScope });
              if (sanitized.changed) {
                if (sanitized.value === null) cursor.delete();
                else cursor.update(sanitized.value);
              }
              cursor.continue();
            };
          }
        };
      })));
    }

    // Old OpenBiliClaw sessions can survive a product switch in browser-owned
    // storage. Purge their delight queue before any backend hydration, and keep
    // normalizeDelight as the final synchronous guard while IndexedDB cleanup
    // finishes in the background.
    purgeLegacyDelightLocalStorage();
    void purgeLegacyDelightIndexedDb();

    const PENDING_REQUEST_IDS_KEY = "openbiliclaw.webui.pendingRequestIds";
    const pendingRequestIds = new Map();

    function newRequestId() {
      const cryptoApi = globalThis.crypto;
      if (typeof cryptoApi?.randomUUID === "function") {
        try { return cryptoApi.randomUUID(); } catch {}
      }

      const bytes = new Uint8Array(16);
      let securelyFilled = false;
      if (typeof cryptoApi?.getRandomValues === "function") {
        try {
          cryptoApi.getRandomValues(bytes);
          securelyFilled = true;
        } catch {}
      }
      if (!securelyFilled) {
        // Last-resort compatibility path for restricted/legacy browser contexts.
        // It is not cryptographic, but remains an RFC 4122 UUID accepted by the
        // backend and mixes time into Math.random so retries do not share a key.
        const timestamp = Date.now();
        const highResolution = Math.floor(globalThis.performance?.now?.() || 0);
        for (let index = 0; index < bytes.length; index += 1) {
          const timeByte = Math.floor(timestamp / (2 ** ((index % 6) * 8))) & 0xff;
          const timerByte = Math.floor(highResolution / (2 ** ((index % 4) * 8))) & 0xff;
          bytes[index] = Math.floor(Math.random() * 256) ^ timeByte ^ timerByte;
        }
      }
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }

    function loadPendingRequestIds() {
      try {
        const parsed = JSON.parse(storageGet(PENDING_REQUEST_IDS_KEY) || "{}");
        if (!parsed || typeof parsed !== "object") return;
        Object.entries(parsed).forEach(([key, value]) => {
          if (typeof value === "string" && value) pendingRequestIds.set(key, value);
        });
      } catch {}
    }

    function persistPendingRequestIds() {
      storageSet(PENDING_REQUEST_IDS_KEY, JSON.stringify(Object.fromEntries(pendingRequestIds)));
    }

    function rememberPendingRequestId(namespace, identity) {
      loadPendingRequestIds();
      const key = `${namespace}:${identity}`;
      const existing = pendingRequestIds.get(key);
      if (existing) return { key, requestId: existing };
      const requestId = newRequestId();
      pendingRequestIds.set(key, requestId);
      persistPendingRequestIds();
      return { key, requestId };
    }

    function forgetPendingRequestId(pending) {
      if (!pending || pendingRequestIds.get(pending.key) !== pending.requestId) return;
      pendingRequestIds.delete(pending.key);
      persistPendingRequestIds();
    }

    const AUTO_LOAD_ON_SCROLL_KEY = "openbiliclaw.webui.autoLoadOnScroll";
    const AUTO_LOAD_COOLDOWN_MS = 8000;
    const DESKTOP_EAGER_COVER_COUNT = 4;
    state.autoLoadOnScroll = storageGet(AUTO_LOAD_ON_SCROLL_KEY) !== "0";
    const THEME_STORAGE_KEY = "obc.theme";
    const THEME_HUE_STORAGE_KEY = "obc.themeHue";
    const THEME_OPTIONS = ["auto", "light", "dark"];
    const THEME_LABELS = { auto: "跟随系统", light: "浅色", dark: "深色" };
    const THEME_GLYPHS = { auto: "◐", light: "☼", dark: "☾" };
    state.themeMode = THEME_OPTIONS.includes(storageGet(THEME_STORAGE_KEY)) ? storageGet(THEME_STORAGE_KEY) : "auto";
    const _storedHue = parseInt(storageGet(THEME_HUE_STORAGE_KEY), 10);
    // Number.isFinite guard so a persisted hue of 0 (烈焰红) survives reload instead of falling back to 20.
    state.themeHue = Number.isFinite(_storedHue) ? _storedHue : 20;
    const ACCENT_STORAGE_KEY = "obc.accentStyle";
    const ACCENT_OPTIONS = ["modern", "classic"];
    const THEME_NOTICE_DISMISSED_KEY = "obc.noticeDismissed";
    // 8 秒覆盖约 55 个中文字的阅读与按钮扫视；hover / focus 会暂停。
    // 校准：2026-07-15 桌面端人工走查，兼顾可读性与不长期遮挡内容。
    const THEME_NOTICE_DURATION_MS = 8000;
    // 迁移：已有自定义色相的老用户默认 modern，保留色相；新用户默认 classic
    const _hasCustomHue = storageGet(THEME_HUE_STORAGE_KEY) !== "";
    const _storedAccent = storageGet(ACCENT_STORAGE_KEY);
    state.accentStyle = ACCENT_OPTIONS.includes(_storedAccent)
      ? _storedAccent
      : _hasCustomHue ? "modern" : "classic";
    if (!ACCENT_OPTIONS.includes(_storedAccent)) {
      storageSet(ACCENT_STORAGE_KEY, state.accentStyle);
    }
    const SIDE_DRAWER_OPEN_KEY = "openbiliclaw.sideDrawerOpen";
    const HOME_MODE_STORAGE_KEY = "newsclaw.webui.homeMode";
    state.homeMode = storageGet(HOME_MODE_STORAGE_KEY) === "materials" ? "materials" : "events";
    const DELIGHT_QUEUE_LIMIT_KEY = "openbiliclaw.webui.delightQueueLimit";
    // 加载更多一次向后端请求的条数（后端 append 端点固定 limit=10）；
    // 返回少于这个数说明候选池当轮见底，据此切换文案并等待补货重试。
    const APPEND_BATCH_SIZE = 10;
    const APPEND_SKELETON_COUNT = 4;
    let autoLoadCheckRaf = 0;
    let autoLoadCheckFallbackTimer = 0;
    let autoLoadCooldownTimer = 0;
    let appendMoreInFlight = false;
    let reshuffleInFlight = false;
    let lastAutoLoadAt = 0;
    let recommendationGridBottomReady = false;
    let recommendationGridScrollIntent = false;
    let recommendationGridBottomArmed = true;
    let _cachedLanIp = "";
    // 惊喜卡自动轮播间隔。原值 4s 是初版占位，实测太快：一张卡的标题 + 推荐理由 + 正文
    // 摘录读下来就要十几秒，还没看清就被换走。60s 给足阅读时间，想快进有拖拽和上一条 /
    // 下一条。与移动端 web/js/views/recommend.js 的同名常量保持一致。
    const DELIGHT_AUTO_ADVANCE_MS = 60000;
    let _delightAutoTimer = null;
    let _delightSwipeStartX = 0;
    const _delightStatusCache = new Map();
    const trackedObservationRecords = new Map();
    const trackedObservationHydratedItems = new Set();
    const trackedObservationEpochs = new Map();
    let delightFeedbackInFlight = false;

    function bindAboutButton() {
      const button = $("#aboutButton");
      if (!(button instanceof HTMLElement)) return;
      button.addEventListener("click", () => openPanel("aboutOverlay"));
    }

    function normalizeBackendHost(host) {
      const trimmed = String(host || "").trim();
      if (!trimmed) return "127.0.0.1";
      try { return new URL(trimmed).hostname || "127.0.0.1"; } catch { return trimmed.replace(/^https?:\/\//, "").replace(/\/.*$/, ""); }
    }

    function safeBind(selector, eventName, handler) {
      const element = $(selector);
      if (!element) { showFatal(new Error(`缺少元素 ${selector}`), "绑定交互"); return; }
      element.addEventListener(eventName, handler);
    }

    function locationApiDefault() {
      try {
        const loc = window.location;
        if (loc && /^https?:$/.test(loc.protocol) && loc.hostname) {
          return { host: loc.hostname, port: loc.port || (loc.protocol === "https:" ? "443" : "80") };
        }
      } catch { /* file:// or no window — fall through */ }
      return { host: "127.0.0.1", port: "8420" };
    }

    function getApiBase() {
      // Default to a *relative* same-origin path so the request carries the page
      // scheme/host/port exactly (correct under an HTTPS reverse proxy and PWA
      // launch) and the HttpOnly session cookie is sent automatically. An
      // explicit saved/typed backend setting still wins (cross-origin mode).
      const typedHost = ($("#backendHost")?.value || storageGet("openbiliclaw.webui.backendHost") || "").trim();
      const typedPort = String($("#backendPort")?.value || storageGet("openbiliclaw.webui.backendPort") || "").trim();
      if (!typedHost && !typedPort) {
        return "/api";
      }
      const def = locationApiDefault();
      const host = normalizeBackendHost(typedHost || def.host);
      const port = (typedPort || def.port).trim() || def.port;
      const proto = (typeof location !== "undefined" && location.protocol === "https:") ? "https" : "http";
      return `${proto}://${host}:${port}/api`;
    }

    function restoreBackendEndpoint() {
      const host = storageGet("openbiliclaw.webui.backendHost");
      const port = storageGet("openbiliclaw.webui.backendPort");
      if (host) setInput("backendHost", normalizeBackendHost(host));
      if (port) setInput("backendPort", port);
    }

    function persistBackendEndpoint() {
      const def = locationApiDefault();
      const host = normalizeBackendHost($("#backendHost")?.value || def.host);
      const port = String($("#backendPort")?.value || def.port).trim() || def.port;
      setInput("backendHost", host);
      setInput("backendPort", port);
      storageSet("openbiliclaw.webui.backendHost", host);
      storageSet("openbiliclaw.webui.backendPort", port);
      return { host, port };
    }

    function getDelightQueueLimit() {
      const raw = $("#delightQueueLimit")?.value || storageGet(DELIGHT_QUEUE_LIMIT_KEY) || "20";
      const limit = Number.parseInt(String(raw), 10);
      if (!Number.isFinite(limit)) return 20;
      return Math.max(1, Math.min(100, limit));
    }

    function restoreFrontendSettings(config = state.config || {}) {
      const configuredLimit = config.scheduler?.delight_queue_limit;
      const limit = configuredLimit || storageGet(DELIGHT_QUEUE_LIMIT_KEY) || "20";
      setInput("delightQueueLimit", String(limit));
      applyThemeMode(state.themeMode);
      applyThemeHue(state.themeHue);
      applyAccentStyle(state.accentStyle);
      renderThemeHueControls();
      renderAutoLoadOnScrollToggle();
      syncAutoLoadBehavior();
    }

    function persistFrontendSettings() {
      const limit = getDelightQueueLimit();
      setInput("delightQueueLimit", String(limit));
      storageSet(DELIGHT_QUEUE_LIMIT_KEY, String(limit));
      storageSet(THEME_STORAGE_KEY, state.themeMode);
      storageSet(THEME_HUE_STORAGE_KEY, String(state.themeHue));
      storageSet(ACCENT_STORAGE_KEY, state.accentStyle);
      storageSet(AUTO_LOAD_ON_SCROLL_KEY, state.autoLoadOnScroll ? "1" : "0");
      applyThemeMode(state.themeMode);
      applyThemeHue(state.themeHue);
      applyAccentStyle(state.accentStyle);
      renderThemeHueControls();
      renderAutoLoadOnScrollToggle();
      syncAutoLoadBehavior();
      return { delightQueueLimit: limit, themeMode: state.themeMode, accentStyle: state.accentStyle, autoLoadOnScroll: state.autoLoadOnScroll };
    }

    function getRuntimeStreamUrl() {
      const base = getApiBase();
      let url;
      if (base.startsWith("/")) {
        // relative same-origin base → build an absolute ws(s) URL from the page
        const proto = (typeof location !== "undefined" && location.protocol === "https:") ? "wss" : "ws";
        const host = (typeof location !== "undefined" && location.host) || "127.0.0.1:8420";
        url = `${proto}://${host}${base}/runtime-stream`;
      } else {
        url = `${base.replace(/^http/, "ws")}/runtime-stream`;
      }
      // cross-origin handshake can't send a cookie → carry the bearer token
      return appendToken(url);
    }

    // ── Password gate (login overlay) ────────────────────────────
    let _authOverlayShown = false;
    const SESSION_TOKEN_KEY = "openbiliclaw.session_token";

    // Cross-origin mode: the desktop UI points at a backend on a *different*
    // origin, so the same-origin cookie isn't sent. The server then issues a
    // finite bearer token (allowed_bearer_origins + ttl>0); we keep it in
    // sessionStorage and attach it as Authorization / ?token= (review r1#5).
    function isCrossOriginBase() {
      const base = getApiBase();
      if (!base || base.startsWith("/")) return false;
      try {
        return new URL(base).origin !== location.origin;
      } catch {
        return false;
      }
    }

    function getSessionToken() {
      if (!isCrossOriginBase()) return "";
      try {
        return sessionStorage.getItem(SESSION_TOKEN_KEY) || "";
      } catch {
        return "";
      }
    }

    function setSessionToken(token) {
      try {
        if (token) sessionStorage.setItem(SESSION_TOKEN_KEY, token);
        else sessionStorage.removeItem(SESSION_TOKEN_KEY);
      } catch { /* sessionStorage unavailable */ }
    }

    function withBearer(headers) {
      const token = getSessionToken();
      return token ? { ...(headers || {}), Authorization: `Bearer ${token}` } : (headers || {});
    }

    function appendToken(url) {
      const token = getSessionToken();
      if (!token) return url;
      return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token);
    }

    async function fetchAuthStatus() {
      const base = getApiBase() || DEFAULT_API_BASE;
      const controller = new AbortController();
      const timeoutId = window.setTimeout(() => controller.abort(), 5000);
      try {
        const res = await fetch(`${base}/auth/status`, {
          credentials: "same-origin",
          headers: withBearer(),
          signal: controller.signal,
        });
        if (!res.ok) throw new Error(`/auth/status 请求失败：HTTP ${res.status}`);
        const status = await res.json();
        if (!status || typeof status !== "object" || Array.isArray(status)) {
          throw new Error("/auth/status 返回了无效数据。");
        }
        return status;
      } catch (error) {
        if (error?.name === "AbortError") throw new Error("/auth/status 请求超时，请稍后重试。");
        throw error;
      } finally {
        window.clearTimeout(timeoutId);
      }
    }

    function handleAuthRequired() {
      // Mid-session token loss (expired / revoked): reload after re-login.
      showLoginOverlay();
    }

    function showLoginOverlay(onSuccess) {
      if (_authOverlayShown) return;
      _authOverlayShown = true;
      const overlay = document.createElement("div");
      overlay.id = "authOverlay";
      overlay.className = "auth-overlay";
      overlay.setAttribute("role", "dialog");
      overlay.setAttribute("aria-modal", "true");
      overlay.innerHTML =
        '<form id="authForm" class="auth-form" autocomplete="off">' +
        '<h2 class="auth-title">NewsClaw</h2>' +
        '<p class="auth-copy">请输入访问密码</p>' +
        '<input id="authPassword" type="password" placeholder="密码" autocomplete="current-password" ' +
        'aria-label="访问密码" class="auth-input">' +
        '<button class="auth-submit" type="submit">登录</button>' +
        '<p id="authError" class="auth-error" role="alert" hidden></p>' +
        "</form>";
      document.body.appendChild(overlay);
      const input = overlay.querySelector("#authPassword");
      const button = overlay.querySelector("button");
      const errorEl = overlay.querySelector("#authError");
      input?.focus();

      const showError = (msg) => {
        if (!errorEl) return;
        errorEl.textContent = msg;
        errorEl.hidden = false;
        input?.select();
      };

      overlay.querySelector("#authForm")?.addEventListener("submit", async (event) => {
        event.preventDefault();
        const password = input?.value || "";
        if (!password) { showError("请输入密码"); return; }
        if (button) { button.disabled = true; button.textContent = "登录中…"; }
        if (errorEl) errorEl.hidden = true;
        try {
          const base = getApiBase() || DEFAULT_API_BASE;
          const res = await fetch(`${base}/auth/login`, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password }),
          });
          const data = await res.json().catch(() => null);
          if (res.ok && data?.ok) {
            // Cross-origin bearer mode: the server returns a finite token here.
            if (data.token) setSessionToken(data.token);
            overlay.remove();
            _authOverlayShown = false;
            if (typeof onSuccess === "function") onSuccess();
            else location.reload();
            return;
          }
          if (res.status === 403) showError("此来源不被允许跨源登录（需配置 allowed_bearer_origins）");
          else if (res.status === 400) showError("跨源登录需设置有限有效期（session_ttl_hours>0）");
          else showError(res.status === 429 ? "尝试过于频繁，请稍后再试" : "密码错误");
        } catch {
          showError("无法连接后端，请稍后重试");
        } finally {
          if (button) { button.disabled = false; button.textContent = "登录"; }
        }
      });
    }

    function ensureAuthenticated() {
      return fetchAuthStatus().then((status) => {
        if (status && status.enabled && status.authenticated === false) {
          return new Promise((resolve) => showLoginOverlay(resolve));
        }
        return undefined;
      });
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
    }

    // Decode source-provided entities for display text only; every later HTML or attribute output must still escape by context.
    function decodeHtmlEntities(value) {
      return String(value ?? "").replace(/&(#x?[0-9a-fA-F]+|amp|lt|gt|quot|apos|#39);/g, (match, entity) => {
        if (entity === "amp") return "&";
        if (entity === "lt") return "<";
        if (entity === "gt") return ">";
        if (entity === "quot") return '"';
        if (entity === "apos" || entity === "#39") return "'";
        if (entity.startsWith("#x")) {
          const codePoint = Number.parseInt(entity.slice(2), 16);
          return Number.isFinite(codePoint) ? String.fromCodePoint(codePoint) : match;
        }
        if (entity.startsWith("#")) {
          const codePoint = Number.parseInt(entity.slice(1), 10);
          return Number.isFinite(codePoint) ? String.fromCodePoint(codePoint) : match;
        }
        return match;
      });
    }

    function urlHostMatches(url, hostnames) {
      const text = String(url || "").trim();
      if (!text) return false;
      try {
        const candidate = /^[a-z][a-z0-9+.-]*:\/\//i.test(text) ? text : `https://${text}`;
        const host = new URL(candidate).hostname.toLowerCase();
        return hostnames.some((hostname) => host === hostname || host.endsWith(`.${hostname}`));
      } catch {
        return false;
      }
    }

    function normalizeSourcePlatform(item) {
      const explicitRaw = String(item?.source_platform ?? item?.media ?? item?.source_name ?? item?.platform ?? "").trim();
      const explicit = explicitRaw.toLowerCase();
      if (platformAliases[explicit] || platformAliases[explicitRaw]) return platformAliases[explicit] || platformAliases[explicitRaw];
      if (explicitRaw) return explicitRaw;
      const url = String(item?.content_url ?? item?.canonical_url ?? "").trim().toLowerCase();
      if (url) {
        if (url.includes("bilibili.com") || url.includes("b23.tv")) return "bilibili";
        if (url.includes("xiaohongshu.com") || url.includes("xhslink.com")) return "xiaohongshu";
        if (url.includes("douyin.com")) return "douyin";
        if (url.includes("youtube.com") || url.includes("youtu.be")) return "youtube";
        if (urlHostMatches(url, ["x.com", "twitter.com"])) return "twitter";
        if (urlHostMatches(url, ["zhihu.com", "zhuanlan.zhihu.com"])) return "zhihu";
        if (urlHostMatches(url, ["reddit.com", "redd.it"])) return "reddit";
        if (urlHostMatches(url, ["linux.do"])) return "linuxdo";
        return "web";
      }
      if (String(item?.bvid ?? "").trim()) return "bilibili";
      return explicit || "news";
    }

    function formatDuration(seconds) {
      const total = Math.floor(Number(seconds) || 0);
      if (total <= 0) return "";
      const hours = Math.floor(total / 3600);
      const minutes = Math.floor((total % 3600) / 60);
      const secondsPart = total % 60;
      if (hours > 0) {
        return `${hours}:${String(minutes).padStart(2, "0")}:${String(secondsPart).padStart(2, "0")}`;
      }
      return `${minutes}:${String(secondsPart).padStart(2, "0")}`;
    }

    function formatCountCn(n) {
      const value = Math.floor(Number(n) || 0);
      if (value <= 0) return "";
      if (value >= 100000000) {
        return `${(Math.floor((value / 100000000) * 10) / 10).toFixed(1).replace(/\.0$/, "")}亿`;
      }
      if (value >= 10000) {
        return `${(Math.floor((value / 10000) * 10) / 10).toFixed(1).replace(/\.0$/, "")}万`;
      }
      return String(value);
    }

    function formatPublishedTime(item, now = Date.now()) {
      const parsed = Date.parse(String(item?.published_at || ""));
      if (Number.isFinite(parsed)) {
        const diff = now - parsed;
        if (diff >= -300_000 && diff < 60_000) return "刚刚";
        if (diff >= 0 && diff < 86_400_000) return `${Math.max(1, Math.floor(diff / 3_600_000))} 小时前`;
        if (diff >= 0 && diff < 604_800_000) return `${Math.floor(diff / 86_400_000)} 天前`;
        const date = new Date(parsed);
        const current = new Date(now);
        if (date.getFullYear() === current.getFullYear()) return `${date.getMonth() + 1}月${date.getDate()}日`;
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
      }
      return String(item?.published_label || "").replace(/\s+/g, " ").trim().slice(0, 64);
    }

    // Legacy content_cache rows persisted before issue #79 still carry raw
    // `answer_<id>` / `zhihu_<id>` titles. Derive something readable from the
    // body text (first sentence), else a generic label, so the card header is
    // never a bare ID even without re-fetching.
    const ID_TITLE_RE = /^(answer|article|question|zhihu)_\S+$/;
    const ZHIHU_TITLE_PLACEHOLDERS = { answer: "来自知乎的回答", article: "来自知乎的文章", question: "来自知乎的提问" };
    function displayRecommendationTitle(rawTitle, bodyText, contentType) {
      const title = String(rawTitle || "").trim();
      if (title && !ID_TITLE_RE.test(title)) return title;
      const body = String(bodyText || "").trim();
      if (body) {
        const first = (body.split(/[。！？!?\n]/, 1)[0] || "").trim() || body;
        return first.length > 40 ? `${first.slice(0, 40)}…` : first;
      }
      return ZHIHU_TITLE_PLACEHOLDERS[contentType] || (title || "未命名内容");
    }

    function normalizeDisplayTags(value) {
      if (!Array.isArray(value)) return [];
      const seen = new Set();
      const tags = [];
      for (const raw of value) {
        const type = String(raw?.type || "").trim().toLowerCase();
        const label = decodeHtmlEntities(raw?.label ?? "").replace(/\s+/g, " ").trim().slice(0, 28);
        if (!DISPLAY_TAG_TYPES.has(type) || !label) continue;
        const key = `${type}\u0000${label.toLocaleLowerCase("zh-Hans-CN")}`;
        if (seen.has(key)) continue;
        seen.add(key);
        tags.push({
          type,
          label,
          origin: String(raw?.origin || "").trim().slice(0, 32)
        });
        if (tags.length === 4) break;
      }
      return tags;
    }

    const RECOMMENDATION_PRIORITY_LEVEL_WEIGHT = {
      urgent: 4,
      high: 3,
      normal: 2,
      low: 1,
    };

    function normalizeRecommendationPriority(item) {
      const decision = item?.decision_priority && typeof item.decision_priority === "object"
        ? item.decision_priority
        : {};
      const priority = item?.priority && typeof item.priority === "object"
        ? item.priority
        : {};
      const level = String(
        decision.level ?? priority.level ?? item?.priority_level ?? "normal"
      ).trim().toLowerCase() || "normal";
      const scoreValue = decision.score ?? priority.score ?? item?.priority_score;
      const declaredHasScore = decision.has_score ?? priority.has_score;
      const hasScore = declaredHasScore == null
        ? scoreValue != null && scoreValue !== "" && Number.isFinite(Number(scoreValue))
        : Boolean(declaredHasScore);
      const rawScore = Number(scoreValue ?? 0);
      const score = Number.isFinite(rawScore) ? Math.max(0, Math.min(1, rawScore)) : 0;
      const label = decodeHtmlEntities(
        decision.label ?? priority.label ?? item?.priority_label ?? ""
      ).replace(/\s+/g, " ").trim();
      const reason = decodeHtmlEntities(
        decision.reason ?? priority.reason ?? item?.priority_reason ?? ""
      ).replace(/\s+/g, " ").trim();
      const signals = decision.signals && typeof decision.signals === "object"
        ? decision.signals
        : priority.signals && typeof priority.signals === "object"
          ? priority.signals
          : item?.priority_signals && typeof item.priority_signals === "object"
            ? item.priority_signals
            : {};
      return { level, score, has_score: hasScore, label, reason, signals };
    }

    function recommendationPriorityScore(item) {
      const priority = normalizeRecommendationPriority(item);
      return priority.has_score
        ? priority.score
        : (RECOMMENDATION_PRIORITY_LEVEL_WEIGHT[priority.level] || 0) / 10;
    }

    function sortRecommendationsByPriority(items) {
      return asArray(items)
        .map((item, index) => ({ item, index, score: recommendationPriorityScore(item) }))
        .sort((left, right) => right.score - left.score || left.index - right.index)
        .map(({ item }) => item);
    }

    function normalizeRecommendation(item) {
      const canonical = window.NewsClawSavedSync.normalizeSavedItem(item);
      const contentId = String(canonical.content_id || item?.article_id || "");
      const contentType = String(canonical.content_type || item?.content_type || "article").toLowerCase();
      const bodyText = decodeHtmlEntities(item?.body_text ?? item?.body ?? "");
      const media = decodeHtmlEntities(item?.media ?? item?.source_name ?? item?.publisher ?? item?.up_name ?? "");
      const reporter = decodeHtmlEntities(item?.reporter ?? item?.author_name ?? item?.up ?? "");
      const priority = normalizeRecommendationPriority(item);
      return {
        id: Number(item?.id ?? Date.now()),
        bvid: String(item?.bvid ?? contentId),
        item_key: canonical.item_key,
        content_id: contentId,
        title: displayRecommendationTitle(decodeHtmlEntities(item?.title ?? ""), bodyText, contentType) || "未命名内容",
        up: reporter || media || "媒体信息暂未提供",
        media,
        cover_url: normalizeImageUrl(item?.cover_url ?? item?.cover ?? item?.pic ?? item?.thumbnail_url ?? item?.thumbnail ?? item?.image_url),
        content_url: canonical.content_url || String(item?.canonical_url ?? ""),
        topic: decodeHtmlEntities(item?.topic_label ?? item?.topic ?? "未归类"),
        platform: canonical.source_platform,
        source_platform: canonical.source_platform,
        content_type: contentType,
        body_text: bodyText,
        duration: Number(item?.duration ?? 0) || 0,
        view_count: Number(item?.view_count ?? 0) || 0,
        like_count: Number(item?.like_count ?? 0) || 0,
        danmaku_count: Number(item?.danmaku_count ?? 0) || 0,
        favorite_count: Number(item?.favorite_count ?? 0) || 0,
        comment_count: Number(item?.comment_count ?? 0) || 0,
        share_count: Number(item?.share_count ?? 0) || 0,
        rating_score: Number(item?.rating_score ?? 0) || 0,
        rating_count: Number(item?.rating_count ?? 0) || 0,
        source_rank: Number(item?.source_rank ?? 0) || 0,
        up_mid: Number(item?.up_mid ?? 0) || 0,
        published_at: String(item?.published_at ?? "").trim(),
        published_label: String(item?.published_label ?? "").replace(/\s+/g, " ").trim().slice(0, 64),
        presented: Boolean(item?.presented),
        feedback_type: String(item?.feedback_type ?? item?.feedback ?? ""),
        pool_status: String(item?.pool_status ?? item?.status ?? ""),
        provider: String(item?.provider ?? item?.technical_provider ?? ""),
        content_scope: String(item?.content_scope ?? item?.body_level ?? ""),
        freshness: String(item?.freshness ?? ""),
        topic_group: decodeHtmlEntities(item?.topic_group ?? "").replace(/\s+/g, " ").trim(),
        franchise_key: decodeHtmlEntities(item?.franchise_key ?? "").replace(/\s+/g, " ").trim(),
        style_key: String(item?.style_key ?? "").trim(),
        temporal_class: String(item?.temporal_class ?? "unknown").trim().toLowerCase(),
        temporal_state: String(item?.temporal_state ?? "unknown").trim().toLowerCase(),
        display_tags: normalizeDisplayTags(item?.display_tags),
        retrieved_at: String(item?.retrieved_at ?? ""),
        priority,
        priority_level: priority.level,
        priority_label: priority.label,
        priority_score: priority.score,
        priority_reason: priority.reason,
        priority_signals: priority.signals,
        why_recommended: decodeHtmlEntities(item?.why_recommended ?? ""),
        impact_chain: decodeHtmlEntities(item?.impact_chain ?? ""),
        observation_points: (Array.isArray(item?.observation_points)
          ? item.observation_points
          : String(item?.observation_points || "").trim()
            ? [item.observation_points]
            : [])
          .map((point) => decodeHtmlEntities(point).replace(/\s+/g, " ").trim())
          .filter(Boolean),
        expression: decodeHtmlEntities(item?.expression ?? item?.reason ?? item?.why_for_you ?? ""),
        reason: decodeHtmlEntities(item?.expression ?? item?.reason ?? item?.why_for_you ?? "后端暂未返回解释。")
      };
    }

    function recommendationKey(item) {
      return String(item?.item_key || item?.bvid || item?.content_id || item?.id || "");
    }

    function shouldRemoveRecommendationAfterFeedback(feedbackType) {
      const normalized = String(feedbackType || "").trim().toLowerCase();
      return normalized === "dislike" || normalized === "dismiss";
    }

    function recommendationFeedbackAlreadyCommitted(item, action) {
      return String(action || "").toLowerCase() === "like"
        && String(item?.feedback_type || item?.feedback || "").toLowerCase() === "like";
    }

    function isFeedbackedRecommendation(item) {
      const feedback = String(item?.feedback_type || item?.feedback || "").trim().toLowerCase();
      const poolStatus = String(item?.pool_status || item?.status || "").trim().toLowerCase();
      return shouldRemoveRecommendationAfterFeedback(feedback) || (poolStatus === "feedbacked" && !feedback);
    }

    function normalizeRecommendationList(items) {
      return sortRecommendationsByPriority(
        asArray(items).map(normalizeRecommendation).filter((item) => !isFeedbackedRecommendation(item))
      );
    }

    async function requestJson(path, options = {}) {
      try {
        return await requestJsonStrict(path, { ...options, timeoutMs: options.timeoutMs ?? 15000 });
      } catch {
        return null;
      }
    }

    async function requestJsonStrict(path, options = {}) {
      const base = options.baseUrl || getApiBase() || DEFAULT_API_BASE;
      const { baseUrl, timeoutMs = 60000, signal, ...fetchOptions } = options;
      // Same-origin: send the session cookie + CSRF header on EVERY request
      // (incl. GET) so state-changing GETs like /api/recommendations are
      // covered (§4.8). Cross-origin: attach the bearer token instead.
      fetchOptions.credentials = "same-origin";
      fetchOptions.headers = { ...(fetchOptions.headers || {}), "X-OBC-Auth": "1" };
      fetchOptions.headers = withBearer(fetchOptions.headers);
      const controller = signal ? null : new AbortController();
      const timeoutId = controller ? window.setTimeout(() => controller.abort(), timeoutMs) : null;
      try {
        const response = await fetch(`${base}${path}`, { ...fetchOptions, signal: signal || controller?.signal });
        const contentType = response.headers.get("content-type") || "";
        const details = contentType.includes("application/json") ? await response.json().catch(() => null) : await response.text().catch(() => "");
        if (!response.ok) {
          if (response.status === 401) {
            setSessionToken("");  // drop a stale bearer token before re-login
            handleAuthRequired();
          }
          const error = new Error(configErrorMessage(details) || `${path} 请求失败：HTTP ${response.status}`);
          error.status = response.status;
          error.details = details;
          throw error;
        }
        return details;
      } catch (error) {
        if (error?.name === "AbortError") {
          const timeoutError = new Error(`${path} 请求超时，请稍后刷新确认是否已写入。`);
          timeoutError.name = "TimeoutError";
          timeoutError.code = "request_timeout";
          throw timeoutError;
        }
        throw error;
      } finally {
        if (timeoutId) window.clearTimeout(timeoutId);
      }
    }

    let migrationStatusLoaded = false;
    let migrationBusy = false;
    const MIGRATION_FRONTEND_APPLIED_KEY = "openbiliclaw.webui.appliedMigrationFrontend";

    function setMigrationStatus(message, tone = "neutral") {
      const status = $("#migrationStatus");
      if (status) {
        status.textContent = String(message || "");
        status.dataset.tone = tone;
        status.setAttribute("role", tone === "error" ? "alert" : "status");
      }
      const card = document.querySelector(".settings-migration-card");
      card?.toggleAttribute("aria-busy", tone === "pending");
    }

    function setMigrationBusy(busy) {
      migrationBusy = Boolean(busy);
      ["migrationExportBtn", "migrationImportBtn", "migrationCancelBtn", "migrationImportFile"].forEach((id) => {
        const control = document.getElementById(id);
        if (control && "disabled" in control) control.disabled = migrationBusy;
      });
    }

    function setMigrationPending(staged) {
      const cancel = $("#migrationCancelBtn");
      if (cancel instanceof HTMLButtonElement) {
        cancel.hidden = !staged;
        cancel.disabled = migrationBusy;
      }
    }

    function collectMigrationFrontendSettings() {
      return {
        theme_mode: normalizeThemeMode(state.themeMode),
        theme_hue: Number.isFinite(state.themeHue) ? Math.max(0, Math.min(360, Math.round(state.themeHue))) : 20,
        accent_style: ACCENT_OPTIONS.includes(state.accentStyle) ? state.accentStyle : "classic",
        auto_load_on_scroll: Boolean(state.autoLoadOnScroll),
        side_drawer_open: storageGet(SIDE_DRAWER_OPEN_KEY) !== "0",
      };
    }

    function applyMigrationFrontendSettings(value) {
      if (!value || typeof value !== "object" || Array.isArray(value)) return;
      if (THEME_OPTIONS.includes(value.theme_mode)) {
        setThemeMode(value.theme_mode, { persist: true });
      }
      if (Number.isInteger(value.theme_hue) && value.theme_hue >= 0 && value.theme_hue <= 360) {
        setThemeHue(value.theme_hue, { persist: true });
      }
      if (ACCENT_OPTIONS.includes(value.accent_style)) {
        setAccentStyle(value.accent_style, { persist: true });
      }
      if (typeof value.auto_load_on_scroll === "boolean") {
        setAutoLoadOnScroll(value.auto_load_on_scroll, { persist: true });
      }
      if (typeof value.side_drawer_open === "boolean") {
        storageSet(SIDE_DRAWER_OPEN_KEY, value.side_drawer_open ? "1" : "0");
        if (!isMobileViewport()) setSideDrawerOpen(value.side_drawer_open, { persist: false });
      }
    }

    function applyMigrationFrontendSettingsOnce(result) {
      const migrationReceipt = String(
        result?.migration_id || result?.applied_at || "legacy-applied-migration",
      ).trim();
      if (storageGet(MIGRATION_FRONTEND_APPLIED_KEY) === migrationReceipt) return false;
      applyMigrationFrontendSettings(result?.frontend);
      storageSet(MIGRATION_FRONTEND_APPLIED_KEY, migrationReceipt);
      return true;
    }

    function migrationDownloadFilename(contentDisposition) {
      const value = String(contentDisposition || "");
      const encoded = value.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
      const quoted = value.match(/filename="([^"]+)"/i)?.[1];
      const plain = value.match(/filename=([^;]+)/i)?.[1]?.trim();
      let filename = encoded || quoted || plain || "";
      try { filename = decodeURIComponent(filename); } catch {}
      filename = filename.replace(/[\\/\u0000-\u001f\u007f]/g, "-").trim();
      if (!filename) {
        filename = `newsclaw-${new Date().toISOString().replace(/[:.]/g, "-")}.newsclaw-backup`;
      } else {
        filename = filename.replace(/^openbiliclaw-backup-/i, "newsclaw-backup-");
        if (filename.toLowerCase().endsWith(".obcbackup")) {
          filename = `${filename.slice(0, -".obcbackup".length)}.newsclaw-backup`;
        } else if (!filename.toLowerCase().endsWith(".newsclaw-backup")) {
          filename += ".newsclaw-backup";
        }
      }
      return filename;
    }

    function formatMigrationBytes(value) {
      const bytes = Number(value || 0);
      if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
      if (bytes < 1024) return `${Math.round(bytes)} B`;
      const units = ["KB", "MB", "GB"];
      let scaled = bytes / 1024;
      let index = 0;
      while (scaled >= 1024 && index < units.length - 1) {
        scaled /= 1024;
        index += 1;
      }
      return `${scaled >= 10 ? scaled.toFixed(0) : scaled.toFixed(1)} ${units[index]}`;
    }

    async function requestMigrationArchive(frontend, writable = null) {
      const base = getApiBase() || DEFAULT_API_BASE;
      const controller = new AbortController();
      const timeoutId = window.setTimeout(() => controller.abort(), 30 * 60 * 1000);
      try {
        const response = await fetch(`${base}${ENDPOINTS.migrationExport}`, {
          method: "POST",
          credentials: "same-origin",
          headers: withBearer({
            "Content-Type": "application/json",
            "X-OBC-Auth": "1",
          }),
          body: JSON.stringify({ frontend }),
          signal: controller.signal,
        });
        if (!response.ok) {
          const contentType = response.headers.get("content-type") || "";
          const details = contentType.includes("application/json")
            ? await response.json().catch(() => null)
            : await response.text().catch(() => "");
          if (response.status === 401) {
            setSessionToken("");
            handleAuthRequired();
          }
          const error = new Error(configErrorMessage(details) || `导出失败：HTTP ${response.status}`);
          error.status = response.status;
          error.details = details;
          throw error;
        }
        const filename = migrationDownloadFilename(response.headers.get("content-disposition"));
        const declaredSize = Number(response.headers.get("content-length") || 0);
        if (writable && response.body && typeof response.body.pipeTo === "function") {
          await response.body.pipeTo(writable);
          return { blob: null, filename, size: Number.isFinite(declaredSize) ? declaredSize : 0 };
        }
        const blob = await response.blob();
        if (writable) {
          await writable.write(blob);
          await writable.close();
          return { blob: null, filename, size: blob.size };
        }
        return { blob, filename, size: blob.size };
      } catch (error) {
        if (error?.name === "AbortError") throw new Error("迁移包生成超时，请检查磁盘空间后重试。");
        throw error;
      } finally {
        window.clearTimeout(timeoutId);
      }
    }

    async function exportMigrationData() {
      if (migrationBusy) return;
      const unsavedWarning = settingsDirtyFields.size > 0
        ? `\n\n当前还有 ${settingsDirtyFields.size} 项配置未保存；导出只包含上一次已保存的配置。`
        : "";
      const confirmed = window.confirm(
        "迁移包会包含 API Key、Tushare Token、画像和历史记录，且当前不加密。请只把它保存在可信设备。" + unsavedWarning,
      );
      if (!confirmed) {
        setMigrationStatus("已取消导出，当前数据未发生变化。", "neutral");
        return;
      }
      let writable = null;
      if (typeof window.showSaveFilePicker === "function") {
        try {
          const handle = await window.showSaveFilePicker({
            suggestedName: migrationDownloadFilename(""),
            types: [{
              description: "NewsClaw 迁移包",
              accept: { "application/vnd.newsclaw.backup+zip": [".newsclaw-backup"] },
            }],
          });
          writable = await handle.createWritable();
        } catch (error) {
          if (error?.name === "AbortError") {
            setMigrationStatus("已取消导出，当前数据未发生变化。", "neutral");
            return;
          }
          setMigrationStatus(`无法创建迁移包文件：${error?.message || "浏览器拒绝写入"}`, "error");
          return;
        }
      }
      const button = $("#migrationExportBtn");
      const previousText = button?.textContent || "导出全部信息";
      setMigrationBusy(true);
      if (button) button.textContent = "正在生成迁移包…";
      setMigrationStatus("正在生成一致性快照；数据较多时可能需要几分钟，请勿关闭页面。", "pending");
      try {
        const { blob, filename, size } = await requestMigrationArchive(
          collectMigrationFrontendSettings(),
          writable,
        );
        if (!writable) {
          if (!blob?.size) throw new Error("后端返回了空迁移包。");
          const url = URL.createObjectURL(blob);
          const anchor = document.createElement("a");
          anchor.href = url;
          anchor.download = filename;
          anchor.hidden = true;
          document.body.append(anchor);
          anchor.click();
          anchor.remove();
          window.setTimeout(() => URL.revokeObjectURL(url), 2000);
        }
        const sizeLabel = size > 0 ? `（${formatMigrationBytes(size)}）` : "";
        setMigrationStatus(`迁移包已保存${sizeLabel}，下载完成。迁移完成后请从两台机器上妥善删除该文件。`, "success");
        showToast("迁移包已生成");
      } catch (error) {
        if (writable && typeof writable.abort === "function") {
          try { await writable.abort(error); } catch {}
        }
        const message = configErrorMessage(error?.details) || error?.message || "生成迁移包失败。";
        setMigrationStatus(`导出失败：${message}`, "error");
        showToast("迁移包导出失败");
      } finally {
        setMigrationBusy(false);
        if (button) button.textContent = previousText;
      }
    }

    function migrationEnvironmentWarning(targetNames, sourceNames) {
      const target = Array.isArray(targetNames)
        ? targetNames.filter((name) => typeof name === "string" && name)
        : [];
      const source = Array.isArray(sourceNames)
        ? sourceNames.filter((name) => typeof name === "string" && name)
        : [];
      const targetWarning = target.length
        ? ` 本机当前启用的这些环境变量仍会影响重启后的运行配置：${target.join("、")}。`
        : "";
      const sourceWarning = source.length
        ? ` 来源机通过这些环境变量提供的值没有写入迁移包：${source.join("、")}。`
        : "";
      return targetWarning + sourceWarning;
    }

    async function stageMigrationImport(file) {
      if (migrationBusy || !(file instanceof File)) return;
      const unsavedWarning = settingsDirtyFields.size > 0
        ? `\n\n当前 ${settingsDirtyFields.size} 项未保存设置会被丢弃。`
        : "";
      const confirmed = window.confirm(
        `确定导入“${file.name}”吗？重启后会用迁移包替换当前配置、画像和历史数据；当前数据会保留一份本地回滚副本。${unsavedWarning}`,
      );
      if (!confirmed) {
        setMigrationStatus("已取消导入，当前数据未发生变化。", "neutral");
        return;
      }
      const button = $("#migrationImportBtn");
      const previousText = button?.textContent || "导入迁移包";
      setMigrationBusy(true);
      if (button) button.textContent = "正在校验迁移包…";
      setMigrationStatus(`正在校验 ${file.name}；校验完成前不会改动当前数据。`, "pending");
      const requestId = newRequestId();
      try {
        const result = await requestJsonStrict(ENDPOINTS.migrationImport, {
          method: "POST",
          timeoutMs: 30 * 60 * 1000,
          headers: {
            "Content-Type": "application/octet-stream",
            "X-OBC-Migration-Confirm": "replace-all",
            "X-OBC-Migration-Request-ID": requestId,
          },
          body: file,
        });
        migrationStatusLoaded = true;
        setMigrationPending(true);
        setMigrationStatus(
          `${result?.message || "迁移包已完整校验并暂存。"} 请完全退出并重新启动 NewsClaw；重启前仍使用当前数据。${migrationEnvironmentWarning(result?.target_active_environment_variables, result?.source_omitted_environment_variables)}`,
          "success",
        );
        showToast("迁移包已就绪，请重启 NewsClaw");
      } catch (error) {
        const uncertain = error?.code === "request_timeout" || error?.name === "TimeoutError" || error instanceof TypeError;
        if (uncertain) {
          migrationStatusLoaded = false;
          setMigrationStatus("上传连接中断，正在向后端确认迁移包是否已经暂存…", "pending");
          const normalizedExpected = String(requestId).replaceAll("-", "").toLowerCase();
          let reconciled = null;
          for (let attempt = 0; attempt < 3; attempt += 1) {
            reconciled = await refreshMigrationStatus({ force: true });
            const state = String(reconciled?.state || "");
            const actual = String(reconciled?.request_id || "").replaceAll("-", "").toLowerCase();
            if ((state === "staged" || state === "processing") && actual === normalizedExpected) break;
            if (!["idle", "cancelled"].includes(state) || attempt === 2) break;
            migrationStatusLoaded = false;
            await new Promise((resolve) => window.setTimeout(resolve, 500));
          }
          const normalizedActual = String(reconciled?.request_id || "").replaceAll("-", "").toLowerCase();
          if (reconciled?.state === "staged" && normalizedActual === normalizedExpected) {
            showToast("迁移包已就绪，请重启 NewsClaw");
          } else if (reconciled?.state === "processing" && normalizedActual === normalizedExpected) {
            migrationStatusLoaded = false;
            setMigrationStatus("上传连接已中断，但后端仍在校验本次迁移包；当前数据尚未改动，请稍后重新打开此页确认。", "pending");
          } else if (["idle", "cancelled"].includes(String(reconciled?.state || ""))) {
            setMigrationStatus("上传中断，后端确认没有暂存本次迁移包；当前数据未改动。", "error");
          } else {
            setMigrationStatus("上传结果暂时无法确认。重启前请重新打开此页检查状态；若出现待导入项，可先取消。", "error");
          }
          return;
        }
        const message = configErrorMessage(error?.details) || error?.message || "迁移包无效。";
        setMigrationStatus(`本次导入未暂存，当前在线数据未改动：${message}`, "error");
        showToast("迁移包导入失败");
      } finally {
        setMigrationBusy(false);
        if (button) button.textContent = previousText;
      }
    }

    async function refreshMigrationStatus({ force = false } = {}) {
      if ((migrationBusy && !force) || (migrationStatusLoaded && !force)) return null;
      try {
        const result = await requestJsonStrict(ENDPOINTS.migrationStatus, {
          cache: "no-store",
          timeoutMs: 8000,
        });
        migrationStatusLoaded = true;
        const status = String(result?.state || "idle");
        if (status === "staged") {
          setMigrationPending(true);
          setMigrationStatus(
            `迁移包已校验并暂存。请完全退出并重新启动 NewsClaw；重启前仍使用当前数据。${migrationEnvironmentWarning(result?.target_active_environment_variables, result?.source_omitted_environment_variables)}`,
            "success",
          );
        } else if (status === "applied") {
          applyMigrationFrontendSettingsOnce(result);
          setMigrationPending(false);
          setMigrationStatus(result?.message || "上一个迁移包已成功载入。现在可以删除迁移包文件。", "success");
        } else if (status === "processing") {
          setMigrationPending(false);
          setMigrationStatus(result?.message || "迁移包仍在上传或校验，当前数据尚未改动。", "pending");
        } else if (status === "failed") {
          setMigrationPending(false);
          setMigrationStatus(result?.message || "上一次迁移应用失败，原数据已恢复。", "error");
        } else if (status === "cancelled") {
          setMigrationPending(false);
          setMigrationStatus(result?.message || "待导入迁移包已取消，当前数据未改动。", "neutral");
        } else {
          setMigrationPending(false);
          setMigrationStatus("只能在运行后端的本机操作；导入会先校验，重启后才替换当前数据。", "neutral");
        }
        return result;
      } catch (error) {
        migrationStatusLoaded = false;
        const message = error?.status === 403
          ? "数据迁移只能从运行后端的本机、同源配置页操作。"
          : (configErrorMessage(error?.details) || error?.message || "无法读取迁移状态。");
        setMigrationStatus(message, "error");
        return null;
      }
    }

    async function cancelPendingMigration() {
      if (migrationBusy || !window.confirm("取消待导入迁移包吗？当前在线数据不会发生变化。")) return;
      const button = $("#migrationCancelBtn");
      const previousText = button?.textContent || "取消待导入";
      setMigrationBusy(true);
      if (button) button.textContent = "正在取消…";
      setMigrationStatus("正在删除已校验的暂存副本…", "pending");
      try {
        const result = await requestJsonStrict(ENDPOINTS.migrationPending, {
          method: "DELETE",
          timeoutMs: 15000,
        });
        migrationStatusLoaded = true;
        setMigrationPending(false);
        setMigrationStatus(result?.message || "待导入迁移包已取消，当前数据未改动。", "neutral");
        showToast("已取消待导入迁移包");
      } catch (error) {
        const message = configErrorMessage(error?.details) || error?.message || "取消失败。";
        setMigrationStatus(`取消失败：${message}`, "error");
      } finally {
        setMigrationBusy(false);
        if (button) button.textContent = previousText;
      }
    }

    async function requestJsonWithPendingId(path, namespace, identity, payload, options = {}) {
      const pending = rememberPendingRequestId(namespace, identity);
      const result = await requestJsonStrict(path, {
        ...options,
        method: "POST",
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        body: JSON.stringify({ ...payload, request_id: pending.requestId })
      });
      forgetPendingRequestId(pending);
      return result;
    }

    function configErrorMessage(details) {
      if (!details) return "";
      if (typeof details === "string") return details;
      const issues = details.issues || details.config?.issues || details.detail?.config?.issues;
      if (Array.isArray(issues) && issues.length) {
        return issues.map((issue) => `${issue.severity || "warning"}: ${issue.message || issue.code || JSON.stringify(issue)}`).join("\n");
      }
      if (Array.isArray(details.detail)) {
        return details.detail.map((item) => `${item.loc?.join(".") || "字段"}: ${item.msg || JSON.stringify(item)}`).join("\n");
      }
      return details.message || details.detail?.message || details.detail?.error || details.error || "";
    }

    function presentDegradedConfigRecovery(snapshot) {
      if (snapshot?.degraded !== true) return;
      state.degraded = true;
      setSideHealthState("degraded");
      const guidance = "LLM 配置不可用：当前没有可用的模型 Provider。请补全默认 Provider 的 API Key、模型与所需 Base URL；保存成功后后端会原地恢复。";
      const diagnostic = configErrorMessage(snapshot);
      const configStatus = $("#configStatus");
      if (configStatus) {
        configStatus.setAttribute("role", "alert");
        configStatus.value = diagnostic ? `${guidance}\n诊断：${diagnostic}` : guidance;
      }
      $("#statusLabel").textContent = "模型配置待修复";
      $("#runtimeSummary").textContent = "AI 服务配置有误，推荐功能暂停；请在模型设置修复并保存。";
      if (degradedRecoveryPresented) return;
      degradedRecoveryPresented = true;
      openSettingsPage("models");
      showToast("模型配置不可用，已打开恢复设置");
    }

    const toastManager = {
      items: [], gap: 8, container: null,
      init() {
        this.container = document.getElementById("toastContainer");
        if (!this.container) {
          this.container = document.createElement("div");
          this.container.className = "toast-container";
          document.body.appendChild(this.container);
        }
      },
      showToast(msg, { duration = 2600 } = {}) {
        const el = document.createElement("div");
        el.className = "toast-item entering";
        el.textContent = msg;
        el.addEventListener("click", (e) => this.dismiss(el));
        el.addEventListener("mouseenter", () => { const i = this.items.find(it => it.el === el); if (i) this._pause(i); });
        el.addEventListener("mouseleave", () => { const i = this.items.find(it => it.el === el); if (i) this._resume(i); });
        this.container.appendChild(el);
        const item = { el, timer: null, remaining: duration, started: Date.now(), paused: false, exiting: false };
        this.items.push(item);
        this._reposition();
        void el.offsetHeight;
        el.classList.remove("entering");
        return item;
      },
      _reposition() {
        let bottom = 0;
        for (const item of this.items) {
          if (item.exiting) continue;
          const first = bottom === 0;
          item.el.style.bottom = bottom + "px";
          if (first && !item.reachedBottom) {
            item.reachedBottom = true;
            const elapsed = Date.now() - item.started;
            const actual = Math.max(0, item.remaining - elapsed);
            if (actual < 2000) item.remaining = actual + 2000;
            if (!item.paused) this._startTimer(item);
          }
          bottom += item.el.offsetHeight + this.gap;
        }
      },
      dismiss(el) {
        const item = this.items.find((i) => i.el === el);
        if (!item || item.exiting) return;
        item.exiting = true;
        this._clearTimer(item);
        el.classList.add("exiting");
        el.addEventListener("transitionend", () => {
          const idx = this.items.indexOf(item);
          if (idx >= 0) this.items.splice(idx, 1);
          el.remove();
          this._reposition();
        }, { once: true });
      },
      _startTimer(item) {
        this._clearTimer(item);
        item.started = Date.now();
        item.timer = setTimeout(() => this.dismiss(item.el), item.remaining);
      },
      _clearTimer(item) {
        if (item.timer) { clearTimeout(item.timer); item.timer = null; }
      },
      _pause(item) {
        if (item.paused || item.exiting || !item.reachedBottom) return;
        this._clearTimer(item);
        item.remaining -= Date.now() - item.started;
        item.paused = true;
      },
      _resume(item) {
        if (!item.paused || item.exiting || !item.reachedBottom) return;
        item.paused = false;
        item.started = Date.now();
        item.timer = setTimeout(() => this.dismiss(item.el), Math.max(item.remaining, 2000));
      }
    };

    function setupThemeNotice() {
      if (state.accentStyle !== "classic" || storageGet(THEME_NOTICE_DISMISSED_KEY) === "1") return;
      const notice = $("#themeNotice");
      const dismissButton = $("#themeNoticeDismiss");
      const settingsButton = $("#themeNoticeSettings");
      if (!notice || !dismissButton || !settingsButton || notice.dataset.bound === "1") return;
      notice.dataset.bound = "1";
      let timer = 0;

      const clearTimer = () => {
        if (timer) window.clearTimeout(timer);
        timer = 0;
      };
      const dismiss = () => {
        clearTimer();
        storageSet(THEME_NOTICE_DISMISSED_KEY, "1");
        notice.classList.remove("is-visible");
        window.setTimeout(() => { notice.hidden = true; }, 200);
      };
      const scheduleDismiss = () => {
        clearTimer();
        timer = window.setTimeout(dismiss, THEME_NOTICE_DURATION_MS);
      };

      dismissButton.addEventListener("click", dismiss);
      settingsButton.addEventListener("click", () => {
        dismiss();
        openSettingsPage("frontend");
      });
      notice.addEventListener("mouseenter", clearTimer);
      notice.addEventListener("mouseleave", scheduleDismiss);
      notice.addEventListener("focusin", clearTimer);
      notice.addEventListener("focusout", (event) => {
        if (!notice.contains(event.relatedTarget)) scheduleDismiss();
      });
      notice.hidden = false;
      window.requestAnimationFrame(() => notice.classList.add("is-visible"));
      scheduleDismiss();
    }
    function showToast(message) { toastManager.showToast(message); }
    window.showToast = showToast;// 用于终端测试ToastNotice

    const pendingActions = window.NewsClawPendingActions.createPendingActionCoordinator({
      windowMs: Number(window.__OBC_TEST_UNDO_WINDOW_MS || 10000),
      onCommitError: (error) => {
        const detail = configErrorMessage(error?.details) || error?.message || "反馈提交失败";
        showToast(`${detail}，已恢复原状态。`);
      }
    });
    window.addEventListener("pagehide", () => { void pendingActions.flushAll(); });
    if (typeof BroadcastChannel !== "undefined") {
      recommendationSyncChannel = new BroadcastChannel("newsclaw-desktop-recommendations");
      recommendationSyncChannel.addEventListener("message", (event) => {
        if (event.data?.type === "recommendation-removed") {
          const key = String(event.data.item_key || "");
          const item = state.videos.find((candidate) => recommendationKey(candidate) === key);
          if (item) removeRecommendationFromWorkspace(item);
          return;
        }
        if (event.data?.type === "saved-changed") applySavedChangeAcrossSurfaces(event.data);
      });
      window.addEventListener("pagehide", () => recommendationSyncChannel?.close(), { once: true });
    }

    function describeInitReason(reason) {
      if (!reason || reason === "none") return "";
      return INIT_REASON_TEXT[reason] || `未知初始化状态：${reason}`;
    }

    function initStatusReasonText(status) {
      const reason = String(status?.reason || "");
      const detail = String(status?.detail || "").trim();
      const detailFirst = new Set([
        "analyze_failed",
        "profile_failed",
        "discovery_timeout",
        "discovery_partial",
        "douyin_degraded"
      ]);
      // account-sync keeps llm_not_ready while the live probe is still red,
      // but its detail contains the actual profile-analysis failure.
      if (detail && (detailFirst.has(reason) || detail.startsWith("画像分析失败："))) return detail;
      return describeInitReason(reason) || detail;
    }

    function initEnabledPlatforms(status) {
      const platforms = status?.prerequisites?.enabled_platforms;
      return Array.isArray(platforms) ? platforms.map(String) : [];
    }

    function initSourceLabels(keys) {
      const byKey = new Map(INIT_SOURCE_OPTIONS.map((opt) => [opt.key, opt.label]));
      return (Array.isArray(keys) ? keys : []).map((key) => byKey.get(key) || key);
    }

    function embeddingPhaseHint(prereq) {
      return prereq?.ollama_phase === "starting" ? "Ollama 启动中…" : "";
    }

    function embeddingPullProgressView(status) {
      const prereq = status?.prerequisites || {};
      const active = Boolean(prereq.embedding_repair_running || prereq.embedding_check === "repairing");
      const completed = Number(prereq.embedding_repair_completed || 0);
      const total = Number(prereq.embedding_repair_total || 0);
      const pct = total > 0
        ? Math.max(1, Math.min(99, Math.round((completed * 100) / total)))
        : active ? 1 : 0;
      const label = String(prereq.embedding_pull_status || prereq.embedding_detail || "").trim() || "正在下载向量模型…";
      return { active, pct, label };
    }

    // Embedding download is process-global work and can begin before guided
    // init reserves a run. It still needs the same status poll while the UI is
    // idle, otherwise the first CTA click becomes the only refresh trigger.
    function embeddingPullNeedsPolling(status) {
      return Boolean(
        status &&
          !status.running &&
          !status.initialized &&
          embeddingPullProgressView(status).active,
      );
    }

    function buildInitChecklist(status, selected = null) {
      const prereq = status?.prerequisites || {};
      const enabled = initEnabledPlatforms(status);
      const selectedSources = Array.isArray(selected) ? selected : null;
      // B 站登录只在勾选了 B 站时才是硬前置。
      const biliSelected = selectedSources ? selectedSources.includes("bilibili") : true;
      const embeddingRequired = Boolean(prereq.embedding_required);
      const embeddingHint = [
        embeddingPhaseHint(prereq),
        String(prereq.embedding_pull_status || prereq.embedding_detail || "").trim()
      ].filter(Boolean).join(" ");
      const embeddingCheck = String(prereq.embedding_check || "");
      const embeddingAutoRepairable = ["model_missing", "model_broken", "model_path_encoding"].includes(embeddingCheck);
      const embeddingGuidanceOnly = ["disk_full", "network", "model_oom", "provider_error"].includes(embeddingCheck);
      // label 必须反映探测的真实结果——固定写“已登录”的条目名一旦不再是红 ✗，
      // 用户就会把它读成“已经登录了”。
      const biliOk = Boolean(prereq.bilibili_logged_in);
      const biliState = biliOk ? "B 站已登录" : "B 站登录检测未通过";
      const biliDetail = String(prereq.bilibili_detail || "").trim();
      return [
        {
          key: "bilibili",
          label: biliSelected ? biliState : `${biliState}（未勾选 B 站，可跳过）`,
          ok: biliOk,
          hard: biliSelected,
          hint:
            (biliDetail ? `${biliDetail} ` : "") +
            "在浏览器里登录 bilibili.com，扩展会自动把 Cookie 同步给后端；不想接 B 站也可以直接取消勾选。"
        },
        {
          key: "llm",
          label: "AI 服务可用",
          ok: Boolean(prereq.llm_ready),
          hard: true,
          hint: "到设置页填好 LLM provider 的 API Key，或确认本地 / 远端模型服务可达。"
        },
        {
          key: "embedding",
          label: embeddingRequired ? "向量模型可用" : "向量模型可用（推荐，非必须）",
          ok: Boolean(prereq.embedding_ready),
          hard: embeddingRequired,
          // Backend-classified cause (embedding_detail, v0.3.155+):
          // Ollama 未运行 / 缺模型 / 模型损坏 / 配置无效 / repairing（下载中，
          // detail 带实时百分比，3s 轮询自动刷新）。
          hint:
            embeddingHint ||
            (embeddingRequired
              ? "本地 Ollama + bge-m3 需要完成一次真实向量请求；模型仍在下载或服务异常时请稍后重试。"
              : "未配置 embedding 时可以先初始化；推荐去重和语义检索会弱一些。"),
          // One-click server-side `ollama pull`; hidden while repairing (the
          // hint already shows live percent).
          repairable: embeddingAutoRepairable || embeddingGuidanceOnly,
          repairLabel: embeddingCheck === "model_path_encoding"
            ? "迁移模型目录并修复"
            : embeddingGuidanceOnly ? "重新检测" : "自动下载向量模型"
        },
        {
          key: "platforms",
          label: selectedSources?.length
            ? `本次初始化来源：${initSourceLabels(selectedSources).join("、")}`
            : enabled.length
              ? `已启用来源：${initSourceLabels(enabled).join("、")}`
              : "数据来源：仅 B 站（可在设置里开启更多平台）",
          ok: true,
          hard: false,
          hint: ""
        }
      ];
    }

    // ── Intra-stage progress + liveness (init-progress-visibility Phase 2) ──
    // MIRROR of the reference implementation in
    // extension/popup/popup-init-control.js — the three GUI surfaces share no
    // module system, so keep the formulas in lock-step when editing either.
    const STAGE_FRACTION_CAP = 0.95;
    // A stage with no real done/total contributes NOTHING to the bar. It used
    // to contribute a flat half-step (0.5), which implied a progress the stage
    // had not made; such stages now render indeterminate instead.
    const STAGE_FRACTION_UNKNOWN = 0;
    // Calibration: backend heartbeat 30s × 3 missed beats (api/app.py
    // _INIT_HEARTBEAT_INTERVAL_SECONDS) — change them in lock-step.
    const INIT_STALL_THRESHOLD_SECONDS = 90;
    // Work-unit stall floor + adaptive slack. The heartbeat period says
    // nothing about how long ONE unit of work legitimately takes: an analysis
    // batch on a slow/remote chat model routinely runs minutes (field report
    // 2026-07-20: 280s per batch — healthy, yet the shared 90s threshold cried
    // "stalled" throughout). Floor generously, then adapt to this run's own
    // cadence. Keep in lock-step with popup-init-control.js.
    const INIT_PROGRESS_STALL_FLOOR_SECONDS = 300;
    const PROGRESS_STALL_SLACK = 1.5;
    function _progressStallThreshold(st, stage) {
      const observed = Math.round((st.slowestProgressIntervalSeconds || 0) * PROGRESS_STALL_SLACK);
      const threshold = Math.max(INIT_PROGRESS_STALL_FLOOR_SECONDS, observed);
      const maxSeconds = Number(stage?.progress?.max_seconds || 0);
      return maxSeconds > 0 ? Math.min(threshold, maxSeconds) : threshold;
    }
    const INIT_EXPECTATION_HINT = "完整画像和首轮可用推荐会严格按顺序生成。总耗时差别很大——取决于你勾了几个平台、拉到多少历史，也取决于 AI 服务的快慢，所以这里不预估时间；运行时会实时显示每一步的已用时和已完成的量。期间可离开此页面，进度会保留。";
    // Said ONCE while the user waits, not repeated on every stage row.
    const INIT_RUNNING_HINT = "只要还在出结果就不会被打断，慢一些是正常的。期间可离开此页面，进度会保留。";

    const _runViewState = new Map();

    function _viewState(runId) {
      let st = _runViewState.get(runId);
      if (!st) {
        st = {
          maxPct: 0,
          lastHeartbeatMark: null, lastHeartbeatChangeMs: 0,
          lastProgressMark: null, lastProgressChangeMs: 0,
          slowestProgressIntervalSeconds: 0
        };
        _runViewState.set(runId, st);
        if (_runViewState.size > 8) {
          const oldest = _runViewState.keys().next().value;
          if (oldest !== runId) _runViewState.delete(oldest);
        }
      }
      return st;
    }

    // Only REAL sub-progress moves the bar. The old elapsed/eta pseudo-progress
    // (1 - e^-t/eta) is gone with the forecasts that fed it: faking a moving
    // bar from a made-up duration is the same lie in another shape. A stage
    // without done/total contributes nothing and renders indeterminate.
    function _runningStageFraction(stage) {
      const total = Number(stage?.progress?.total || 0);
      if (total > 0) {
        const done = Math.max(0, Math.min(Number(stage?.progress?.done || 0), total));
        return Math.min(STAGE_FRACTION_CAP, done / total);
      }
      return STAGE_FRACTION_UNKNOWN;
    }

    // A waiting user needs EVIDENCE OF PROGRESS, not a forecast. A predicted
    // duration we cannot honour is worse than none: every wrong estimate reads
    // as "it broke" (field report 2026-07-20 — stage 2 announced 3 minutes and
    // stage 4 announced 5, both legitimately ran far longer). So the running
    // row reports only observed facts the backend already publishes: how long
    // this stage has been running, and real sub-progress counts when they
    // exist. No estimate, no ceiling, no extrapolation.
    function formatElapsedText(seconds) {
      const s = Math.max(0, Math.floor(Number(seconds) || 0));
      return s < 60 ? "已用时不到 1 分钟" : `已用时 ${Math.floor(s / 60)} 分钟`;
    }

    function stageDetailText(stage) {
      const prog = stage?.progress;
      if (!prog) return "";
      const parts = [];
      const elapsed = Number(prog.elapsed_seconds || 0);
      if (elapsed > 0) parts.push(formatElapsedText(elapsed));
      const total = Number(prog.total || 0);
      if (total > 0) {
        const done = Math.max(0, Math.min(Number(prog.done || 0), total));
        parts.push(`已完成 ${done}/${total}`);
      }
      return parts.join(" · ");
    }

    // Split connection liveness from substantive work progress. A healthy 30s
    // heartbeat must not disguise a provider call that stopped advancing.
    function stalenessView(status, nowMs = Date.now()) {
      if (!status?.running) return { fresh: true, staleSeconds: 0, text: "" };
      const runId = status.run_id ? String(status.run_id) : "";
      const heartbeatAt = status.last_heartbeat_at || status.last_activity;
      const progressAt = status.last_progress_at || status.last_activity;
      if (!runId || !heartbeatAt) {
        return { fresh: true, staleSeconds: 0, text: "● 后端已接单 · 正在建立进度" };
      }
      const st = _viewState(runId);
      const heartbeatMark = `${status.sequence ?? ""}|${heartbeatAt}`;
      if (st.lastHeartbeatMark !== heartbeatMark) {
        st.lastHeartbeatMark = heartbeatMark;
        st.lastHeartbeatChangeMs = nowMs;
      }
      const progressMark = `${status.progress_sequence ?? status.sequence ?? ""}|${progressAt || ""}`;
      if (st.lastProgressMark !== progressMark) {
        // Learn this run's pace from every completed unit (skip the first
        // mark, which measures "since we started watching").
        if (st.lastProgressMark != null && st.lastProgressChangeMs) {
          const interval = Math.max(0, Math.round((nowMs - st.lastProgressChangeMs) / 1000));
          st.slowestProgressIntervalSeconds = Math.max(
            st.slowestProgressIntervalSeconds || 0,
            interval,
          );
        }
        st.lastProgressMark = progressMark;
        st.lastProgressChangeMs = nowMs;
      }
      const heartbeatStale = Math.max(0, Math.round((nowMs - st.lastHeartbeatChangeMs) / 1000));
      const progressStale = Math.max(0, Math.round((nowMs - st.lastProgressChangeMs) / 1000));
      if (heartbeatStale > INIT_STALL_THRESHOLD_SECONDS) {
        const minutes = Math.max(1, Math.round(heartbeatStale / 60));
        return {
          fresh: false,
          staleSeconds: heartbeatStale,
          text: `后端已 ${minutes} 分钟没有心跳，连接可能中断。系统会继续重试；也可以取消后重试。`
        };
      }
      const runningStage = Array.isArray(status.stages)
        ? status.stages.find((stage) => stage?.status === "running")
        : null;
      if (progressStale > _progressStallThreshold(st, runningStage)) {
        const minutes = Math.max(1, Math.round(progressStale / 60));
        return {
          fresh: false,
          staleSeconds: progressStale,
          text: `● 后端在线 · 这一步已等待 ${minutes} 分钟，比本轮此前的节奏慢；AI 或平台可能正卡在一次较慢的请求上，可继续等待或取消。`
        };
      }
      return { fresh: true, staleSeconds: progressStale, text: "● 后端在线 · 正在处理" };
    }

    function initProgressView(status, nowMs = Date.now()) {
      const total = status?.total_stages || 4;
      const stages = Array.isArray(status?.stages) ? status.stages : [];
      const doneCount = stages.filter((stage) => stage.status === "ok").length;
      const running = Boolean(status?.running);
      const runId = status?.run_id ? String(status.run_id) : "";
      const st = runId ? _viewState(runId) : null;
      const failedStage = stages.find((stage) => stage.status === "failed" || stage.status === "cancelled");
      const current = status?.current_stage || 0;
      const currentStage = stages.find((stage) => stage.n === current);
      // Indeterminate covers both the backend's explicit flag and any
      // running stage with no real done/total — with the eta gone there is
      // nothing honest left to fill such a bar with.
      const indeterminate = Boolean(
        running &&
          currentStage &&
          (currentStage.progress?.mode === "indeterminate" ||
            !(Number(currentStage.progress?.total || 0) > 0)),
      );
      let stageLabel = currentStage ? `${currentStage.n}/${total} ${currentStage.label}` : "";
      const note = currentStage?.progress?.note;
      if (stageLabel && note) stageLabel += ` · ${note}`;
      const runningStages = stages.filter((stage) => stage.status === "running");
      const inFlight = runningStages.length
        ? runningStages.reduce((sum, stage) => sum + _runningStageFraction(stage), 0) /
          runningStages.length
        : 0;
      const rawPct = ((doneCount + (running ? inFlight : 0)) / total) * 100;
      let pct = Math.max(0, Math.min(100, Math.round(rawPct)));
      if (running) pct = Math.max(pct, 1);
      if (st) {
        st.maxPct = Math.max(st.maxPct, pct);
        pct = st.maxPct;
      }
      return {
        active: running,
        failed: Boolean(failedStage),
        indeterminate,
        pct,
        stageLabel,
        stageDetailText: running ? stageDetailText(currentStage) : "",
        failedReason: failedStage?.reason || ""
      };
    }

    // Human text for a failed/cancelled run. ``status.detail`` carries the
    // backend's stored failure specifics (exception summary / GuidedInitError
    // message, v0.3.156+) — append it so internal_error is diagnosable from
    // the UI instead of only the generic "请稍后重试".
    function initFailureText(status, progress) {
      const base = describeInitReason(status?.reason) || "";
      const detail = String(status?.detail || "").trim();
      const reason = String(status?.reason || "");
      if (
        detail &&
        ([
          "analyze_failed",
          "profile_failed",
          "discovery_timeout",
          "discovery_partial",
          "douyin_degraded"
        ].includes(reason) ||
          detail.startsWith("画像分析失败："))
      ) return detail;
      // Unmapped codes (empty_history / empty_signals / profile_failed …)
      // carry their authoritative human message in detail — show it alone
      // instead of "未知初始化状态：code（message）".
      if (detail && (!base || base.startsWith("未知初始化状态"))) return detail;
      if (base && detail) return `${base}（${detail}）`;
      return base || progress?.failedReason || "初始化未完成，请稍后重试。";
    }

    function selectedInitSourcesFromDom() {
      return Array.from(document.querySelectorAll("input[data-init-source]"))
        .filter((input) => input.checked)
        .map((input) => input.value);
    }

    function initChecklistMarkup(status, selected = null) {
      if (!status) {
        return '<li class="init-hint-row">点「开始初始化」会先检查 AI 服务、向量模型和新闻源接口权限，通过后再开始。</li>';
      }
      // Post-init the pre-init checklist is irrelevant, and the backend no
      // longer live-probes services for already-initialized status reads —
      // the cached values could read stale-red here (e.g. right after a
      // backend restart while the first pool is still filling). Hide it.
      if (status.initialized) return "";
      return buildInitChecklist(status, selected)
        .map((row) => {
          const mark = row.ok ? "✓" : row.hard ? "✗" : "•";
          const hint = !row.ok && row.hint ? `<p class="init-hint">${escapeHtml(row.hint)}</p>` : "";
          const repair = !row.ok && row.repairable
            ? `<button class="small-btn init-repair-btn" type="button" data-embedding-repair>${escapeHtml(row.repairLabel || "自动下载向量模型")}</button>`
            : "";
          return `<li class="${row.ok ? "init-ok" : "init-missing"} ${row.hard ? "init-hard" : "init-soft"}"><div class="init-row"><span class="init-mark">${mark}</span><span>${escapeHtml(row.label)}</span></div>${hint}${repair}</li>`;
        })
        .join("");
    }

    // Kick the server-side model pull; the 3s init-status poll then renders
    // live percent on the checklist row (embedding_check="repairing"). The
    // checklist is re-rendered per poll, so the handler is DELEGATED from the
    // <ul> (bound once in renderInitOnboarding) instead of per-button.
    async function handleEmbeddingRepairClick(btn) {
      const originalLabel = btn.textContent || "自动下载向量模型";
      btn.disabled = true;
      btn.textContent = originalLabel === "重新检测" ? "检测中…" : "启动下载…";
      try {
        await requestJsonStrict(ENDPOINTS.embeddingRepair, { method: "POST" });
      } catch (error) {
        // 409 already_running means a pull is in flight — that's the goal
        // state; every other error re-enables the button with the reason.
        if (error?.status !== 409 || error?.details?.error !== "already_running") {
          btn.disabled = false;
          btn.textContent = originalLabel;
          state.initReason = error?.details?.detail || error?.message || "向量模型修复启动失败。";
          renderInitOnboarding();
          return;
        }
      }
      void refreshInitStatus({ schedule: true });
    }

    function initSourcesMarkup() {
      const selected = state.initSelectedSources
        ? new Set(state.initSelectedSources)
        : new Set(INIT_SOURCE_OPTIONS.filter((opt) => opt.defaultChecked).map((opt) => opt.key));
      const rows = INIT_SOURCE_OPTIONS.map((opt) => {
        const checked = selected.has(opt.key) ? " checked" : "";
        const label = opt.defaultChecked ? `${opt.label}（推荐）` : opt.label;
        return `<label class="init-source-row"><input type="checkbox" value="${escapeHtml(opt.key)}" data-init-source="${escapeHtml(opt.key)}"${checked}><span>${escapeHtml(label)}</span></label>`;
      }).join("");
      const bangumiDisabled = selected.has("bangumi") ? "" : " disabled";
      const bangumiUsername = state.initBangumiUsernameTouched
        ? state.initBangumiUsername
        : state.config?.sources?.bangumi?.username || state.initBangumiUsername || "";
      const bangumiInput = `<label class="init-source-row"><span>Bangumi 公开用户名（可留空，仅启用发现）</span><input id="initBangumiUsername" maxlength="128" autocomplete="off" value="${escapeHtml(bangumiUsername)}"${bangumiDisabled}></label>`;
      // Optional personal access token: identifies the account via /v0/me and
      // reads private collections; when set, the username above is auto-resolved.
      const bangumiTokenInput = `<label class="init-source-row"><span>Bangumi 个人令牌（可留空，推荐：自动识别当前用户，可读私密收藏）</span><input id="initBangumiToken" type="password" maxlength="512" autocomplete="off" value="${escapeHtml(state.initBangumiToken || "")}"${bangumiDisabled}></label>`;
      const bangumiTokenHint = `<p class="init-sources-hint">Bangumi 属于兼容模块，不是新闻爪的新闻来源。需要维护旧数据时，可在 Bangumi 官方页面生成个人令牌；新闻爪正常使用无需配置。</p>`;
      return `<div class="init-sources"><p class="init-sources-title">选择初始化数据来源（至少一个）</p>${rows}${bangumiInput}${bangumiTokenInput}${bangumiTokenHint}<p class="init-sources-hint">${escapeHtml(INIT_SOURCE_LOGIN_HINT)}</p></div>`;
    }

    function initOnboardingPhase(status, progress) {
      if (state.initBusy) return "busy";
      if (Boolean(status?.running)) return "running";
      if (Boolean(status?.initialized)) return "completed";
      if (progress.failed) return "failed";
      return "idle";
    }

    function updateInitOnboardingStatus(section, status, progress, reason, buttonLabel, buttonDisabled) {
      const checklist = section.querySelector(".init-checklist");
      if (checklist) checklist.innerHTML = initChecklistMarkup(status, state.initSelectedSources);
      const progressBox = section.querySelector(".init-progress");
      const progressFill = section.querySelector(".init-progress-fill");
      const progressText = progressBox?.querySelector("p");
      const progressLabel = progress.failed
        ? initFailureText(status, progress)
        : progress.active
          ? progress.label || (progress.indeterminate
            ? progress.stageLabel || "正在初始化"
            : `${progress.stageLabel || "正在初始化"}（${progress.pct}%）`)
          : "等待开始";
      if (progressBox) progressBox.hidden = !(Boolean(status?.running) || progress.failed);
      if (progressFill) {
        progressFill.style.width = progress.indeterminate ? "100%" : `${progress.pct}%`;
      }
      if (progressFill) progressFill.classList.toggle("indeterminate", Boolean(progress.indeterminate));
      if (progressText) {
        progressText.textContent = progressLabel;
        progressText.setAttribute("role", progress.failed ? "alert" : "status");
        progressText.setAttribute("aria-live", progress.failed ? "assertive" : "polite");
      }
      // Liveness line: "● 进行中 (+ typical stage duration)" while the backend
      // keeps writing; amber stall copy after >90s of silence.
      const stallHint = section.querySelector(".init-stall-hint");
      if (stallHint) {
        const staleness = stalenessView(status);
        const stallText = Boolean(status?.running)
          ? staleness.fresh
            ? [staleness.text, progress.stageDetailText].filter(Boolean).join(" · ")
            : staleness.text
          : "";
        stallHint.textContent = stallText;
        stallHint.classList.toggle("stale", Boolean(status?.running) && !staleness.fresh);
        stallHint.hidden = !stallText;
      }
      const reasonText = section.querySelector(".init-reason");
      if (reasonText) {
        reasonText.hidden = !reason;
        reasonText.textContent = reason;
      }
      const startButton = section.querySelector('[data-init-action="start"]');
      if (startButton) {
        startButton.disabled = buttonDisabled;
        startButton.textContent = buttonLabel;
      }
      const cancelButton = section.querySelector('[data-init-action="cancel"]');
      if (cancelButton) {
        cancelButton.hidden = !status?.running;
        cancelButton.disabled = !status?.running;
      }
    }

    function renderInitOnboarding() {
      if (!grid) return;
      grid.innerHTML = `
        <section class="init-onboarding" aria-label="新闻爪首次设置" data-init-phase="news-bootstrap">
          <div class="init-onboarding-copy">
            <p class="eyebrow">NewsClaw setup</p>
            <h3>从零建立你的新闻画像</h3>
            <p class="video-meta">依次连接 AI、验证 Tushare / RSS 新闻源、设置关注方向与避雷边界，再生成第一批真实新闻推荐。不会导入任何旧系统的行为或画像。</p>
          </div>
          <ul class="init-checklist">
            <li class="ok"><span>✓</span><strong>不读取 Cookie，不抓取网页</strong></li>
            <li class="ok"><span>✓</span><strong>凭据只保存在本机并脱敏显示</strong></li>
            <li class="ok"><span>✓</span><strong>只有标题时明确标注“标题级信息”</strong></li>
          </ul>
          <div class="init-actions">
            <button class="small-btn primary" type="button" data-news-setup>打开四步首次设置</button>
            <button class="small-btn" type="button" data-news-source-settings>查看新闻源</button>
          </div>
        </section>`;
      const newsLoadMore = $("#loadMoreBtn");
      if (newsLoadMore) newsLoadMore.hidden = true;
      grid.querySelector("[data-news-setup]")?.addEventListener("click", () => { window.location.href = "/setup"; });
      grid.querySelector("[data-news-source-settings]")?.addEventListener("click", () => { openSettingsPage("sources"); });
      return;
      const status = state.initStatus;
      const progress = initProgressView(status);
      const isRunning = Boolean(status?.running);
      const embeddingPull = embeddingPullProgressView(status);
      const displayProgress = embeddingPull.active && !isRunning
          ? { active: true, failed: false, pct: embeddingPull.pct, label: embeddingPull.label, stageLabel: "" }
        : progress;
      const alreadyInitialized = Boolean(status?.initialized);
      const showProgress = isRunning || displayProgress.failed || embeddingPull.active;
      const reason = displayProgress.failed
          ? (state.initReason || initFailureText(status, displayProgress))
          : (state.initReason || initStatusReasonText(status) || "");
      const phase = initOnboardingPhase(status, displayProgress);
      const buttonLabel = state.initBusy
        ? "检查中…"
          : isRunning
          ? "初始化进行中…"
          : alreadyInitialized
            ? status?.partial_success ? "初始化部分完成" : "已初始化"
            : displayProgress.failed
              ? "重试初始化"
              : "开始初始化";
      const buttonDisabled = state.initBusy || isRunning || alreadyInitialized;
      const staleness = stalenessView(status);
      const stallText = isRunning
        ? staleness.fresh
          ? [staleness.text, displayProgress.stageDetailText].filter(Boolean).join(" · ")
          : staleness.text
        : "";
      // Expectation management near the start button while a run can begin.
      // Idle: orient the user about variability. Running: the one
      // reassurance that is literally true after v0.3.180.
      const expectationText = isRunning
        ? INIT_RUNNING_HINT
        : alreadyInitialized
          ? ""
          : INIT_EXPECTATION_HINT;
      const existing = grid.querySelector(".init-onboarding");
      if (existing?.dataset.initPhase === phase && phase !== "idle" && phase !== "busy") {
        updateInitOnboardingStatus(existing, status, displayProgress, reason, buttonLabel, buttonDisabled);
        const loadMore = $("#loadMoreBtn");
        if (loadMore) loadMore.hidden = true;
        return;
      }
      grid.innerHTML = `
        <section class="init-onboarding" aria-label="引导初始化" data-init-phase="${escapeHtml(phase)}">
          <div class="init-onboarding-copy">
            <p class="eyebrow">Guided init</p>
            <h3>还没完成初始化</h3>
            <p class="video-meta">先检查 AI 服务和新闻源权限，再依次拉取真实新闻、分析偏好、保存完整画像，最后严格基于这份画像生成首轮推荐。</p>
          </div>
          ${isRunning ? "" : initSourcesMarkup()}
          <ul class="init-checklist">${initChecklistMarkup(status, state.initSelectedSources)}</ul>
          <div class="init-progress"${showProgress ? "" : " hidden"}>
            <div class="init-progress-track"><div class="init-progress-fill${displayProgress.indeterminate ? " indeterminate" : ""}" style="width:${displayProgress.indeterminate ? 100 : displayProgress.pct}%"></div></div>
            <p role="${displayProgress.failed ? "alert" : "status"}" aria-live="${displayProgress.failed ? "assertive" : "polite"}" aria-atomic="true">${escapeHtml(displayProgress.failed ? initFailureText(status, displayProgress) : displayProgress.active ? displayProgress.label || (displayProgress.indeterminate ? displayProgress.stageLabel || "正在初始化" : `${displayProgress.stageLabel || "正在初始化"}（${displayProgress.pct}%）`) : "等待开始")}</p>
          </div>
          <p class="init-stall-hint${stallText && !staleness.fresh ? " stale" : ""}"${stallText ? "" : " hidden"}>${escapeHtml(stallText)}</p>
          <p class="init-expectation"${expectationText ? "" : " hidden"}>${escapeHtml(expectationText)}</p>
          <p class="init-reason" role="status" aria-live="polite" aria-atomic="true"${reason ? "" : " hidden"}>${escapeHtml(reason)}</p>
          <div class="init-actions">
            <button class="small-btn primary" type="button" data-init-action="start"${buttonDisabled ? " disabled" : ""}>${escapeHtml(buttonLabel)}</button>
            <button class="small-btn" type="button" data-init-action="cancel"${isRunning ? "" : " hidden"}>取消初始化</button>
            <button class="small-btn" type="button" data-init-action="settings">打开设置</button>
          </div>
        </section>`;
      const loadMore = $("#loadMoreBtn");
      if (loadMore) loadMore.hidden = true;
      grid.querySelector('[data-init-action="start"]')?.addEventListener("click", () => {
        void handleDesktopStartInitClick();
      });
      grid.querySelector('[data-init-action="cancel"]')?.addEventListener("click", (event) => {
        void handleDesktopCancelInitClick(event.currentTarget);
      });
      grid.querySelector('[data-init-action="settings"]')?.addEventListener("click", () => {
        openSettingsPage("sources");
      });
      // Delegated: the checklist's innerHTML is replaced on every status
      // poll, so listeners on the buttons themselves would be lost.
      grid.querySelector(".init-onboarding .init-checklist")?.addEventListener("click", (event) => {
        const btn = event.target.closest?.("[data-embedding-repair]");
        if (btn) void handleEmbeddingRepairClick(btn);
      });
      grid.querySelectorAll("input[data-init-source]").forEach((input) => {
        input.addEventListener("change", () => {
          state.initSelectedSources = selectedInitSourcesFromDom();
          const bangumiChecked = state.initSelectedSources.includes("bangumi");
          const bangumiUsername = grid.querySelector("#initBangumiUsername");
          if (bangumiUsername) bangumiUsername.disabled = !bangumiChecked;
          const bangumiToken = grid.querySelector("#initBangumiToken");
          if (bangumiToken) bangumiToken.disabled = !bangumiChecked;
          // Refresh just the checklist so the B 站 row flips between hard
          // prerequisite and skippable hint as the checkbox changes.
          const checklist = grid.querySelector(".init-onboarding .init-checklist");
          if (checklist) {
            checklist.innerHTML = initChecklistMarkup(state.initStatus, state.initSelectedSources);
          }
        });
      });
      grid.querySelector("#initBangumiUsername")?.addEventListener("input", (event) => {
        state.initBangumiUsername = event.currentTarget.value || "";
        state.initBangumiUsernameTouched = true;
      });
      grid.querySelector("#initBangumiToken")?.addEventListener("input", (event) => {
        state.initBangumiToken = event.currentTarget.value || "";
      });
    }

    function clearInitPolling() {
      if (initPollTimer !== null) {
        window.clearTimeout(initPollTimer);
        initPollTimer = null;
      }
    }

    function scheduleInitStatusRefresh(delayMs = INIT_STATUS_POLL_MS) {
      clearInitPolling();
      if (document.hidden) return;
      initPollTimer = window.setTimeout(() => {
        initPollTimer = null;
        void refreshInitStatus();
      }, delayMs);
    }

    // 初始化状态刷新会被 refresh.pool_updated 高频拽起来（见 handleRuntimeEvent），
    // 补货一轮能打好几次。网格里已经是真实卡片、又不需要退回引导门时，这条路径
    // 只刷新头部 / 库存 / 侧栏，不重绘推荐列表 —— 与 refreshPlatformAvailability
    // 的约定保持一致：库存事件不许碰已加载的卡片。
    function initStatusRenderOptions() {
      if (shouldShowInitOnboarding(state.runtimeStatus)) return {};
      if (grid.querySelector(".init-onboarding") || grid.querySelector(".empty-state")) return {};
      if (!grid.querySelector(".video-card:not(.is-skeleton)")) return {};
      return { preserveVideos: true };
    }

    async function refreshInitStatus({ schedule = true } = {}) {
      if (initRefreshInFlight) {
        initRefreshPending = true;
        return;
      }
      initRefreshInFlight = true;
      clearInitPolling();
      const wasInitialized = Boolean(state.initStatus?.initialized);
      const wasRunning = Boolean(state.initStatus?.running);
      try {
        const status = await requestJsonStrict(ENDPOINTS.initStatus, { timeoutMs: 60000 });
        state.initStatus = status;
        state.initReason = "";
        renderSettingsReinitStatus();
        if (status?.running) {
          renderAll(initStatusRenderOptions());
          scheduleInitStatusRefresh(schedule ? INIT_STATUS_POLL_MS : INIT_STATUS_WATCHDOG_MS);
          return;
        }
        if (status?.initialized) {
          renderAll(initStatusRenderOptions());
          clearInitPolling();
          initRefreshPending = false;
          // Stage 3 makes initialized=true while stage 4 still owns the run.
          // Rehydrate runtime inventory on the running -> terminal edge too;
          // otherwise the recommendation grid can finish while the header and
          // pool rail keep the pre-init runtime snapshot until a page reload.
          if (!wasInitialized || wasRunning) {
            scheduleBackendHydration();
            showToast(
              status?.partial_success
                ? initStatusReasonText(status) ||
                  "初始化部分完成；已采数据已保留并使用，请按提示稍后补齐。你现在可以先进入应用。"
                : "初始化完成，正在加载推荐"
            );
          }
          return;
        }
        renderAll(initStatusRenderOptions());
        if (embeddingPullNeedsPolling(status)) {
          scheduleInitStatusRefresh(schedule ? INIT_STATUS_POLL_MS : INIT_STATUS_WATCHDOG_MS);
        } else if (!status?.running) {
          clearInitPolling();
        }
      } catch (error) {
        scheduleInitStatusRefresh(INIT_STATUS_POLL_MS);
        state.initReason = `暂时无法连接初始化后台：${error?.message || "正在重试"}。已保留当前进度。`;
        renderAll(initStatusRenderOptions());
      } finally {
        initRefreshInFlight = false;
        if (initRefreshPending) {
          initRefreshPending = false;
          void refreshInitStatus({ schedule });
        }
      }
    }

    async function handleDesktopStartInitClick() {
      const selected = selectedInitSourcesFromDom();
      state.initSelectedSources = selected;
      state.initBusy = true;
      state.initReason = "";
      renderAll();
      let status = null;
      try {
        status = await requestJsonStrict(ENDPOINTS.initStatus, { timeoutMs: 60000 });
        state.initStatus = status;
      } catch (error) {
        state.initReason = error?.message || "前置检查没拉到，稍后再试。";
        state.initBusy = false;
        renderAll();
        return;
      }
      if (status.running) {
        state.initBusy = false;
        renderAll();
        clearInitPolling();
        scheduleInitStatusRefresh(INIT_STATUS_START_POLL_MS);
        return;
      }
      if (status.initialized) {
        state.initBusy = false;
        state.initReason = status?.partial_success ? initStatusReasonText(status) : "";
        scheduleBackendHydration();
        renderAll();
        return;
      }
      if (embeddingPullNeedsPolling(status)) {
        state.initBusy = false;
        renderAll();
        scheduleInitStatusRefresh(INIT_STATUS_POLL_MS);
        return;
      }
      if (!selected.length) {
        state.initReason = INIT_REASON_TEXT.no_sources_selected;
        state.initBusy = false;
        renderAll();
        return;
      }
      const bangumiUsername = String(
        $("#initBangumiUsername")?.value || state.initBangumiUsername || ""
      ).trim();
      // Send an explicit username only when the user deliberately edited the
      // field, or a successful /api/config prefill gave us the value to clear.
      // Otherwise omit it so the backend keeps the configured username instead
      // of erasing it with an empty, never-prefilled field.
      const sendBangumiUsername =
        state.initBangumiUsernameTouched &&
        (bangumiUsername !== "" || state.initBangumiUsernamePrefilled);
      const bangumiToken = String(
        $("#initBangumiToken")?.value || state.initBangumiToken || ""
      ).trim();
      // No client-side Bangumi-only admission check here on purpose. The
      // backend owns a THREE-tier account ladder (token → explicit username →
      // browser-extension-reported identity); a local "username or token
      // required" copy of it can't see the third tier and silently blocked
      // zero-config extension users from ever reaching /api/init. The backend
      // answers 409 no_profile_signal_sources when all three are genuinely
      // missing, and the catch below renders it.
      if (selected.includes("bilibili") && !status?.prerequisites?.bilibili_logged_in) {
        state.initReason = "新闻源尚未通过验证，请打开四步首次设置检查接口权限。";
        state.initBusy = false;
        renderAll();
        return;
      }
      if (!status.can_start) {
        state.initReason = initStatusReasonText(status) || "以下条件未满足，无法开始初始化。";
        state.initBusy = false;
        renderAll();
        return;
      }
      try {
        const payload = { sources: selected };
        if (selected.includes("bangumi") && (sendBangumiUsername || bangumiToken)) {
          const bangumi = {};
          if (sendBangumiUsername) bangumi.username = bangumiUsername;
          // Only send a token the user actually typed; omit otherwise so the
          // backend keeps any configured token.
          if (bangumiToken) bangumi.access_token = bangumiToken;
          payload.source_options = { bangumi };
        }
        const started = await requestJsonStrict(ENDPOINTS.startInit, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          timeoutMs: 60000
        });
        state.initStatus = { ...(state.initStatus || {}), ...started };
        state.initBusy = false;
        // The 202 response may carry backend warnings (e.g. Bangumi selected
        // without a public username → discovery-only). Surface them in the
        // onboarding reason and the toast instead of a bare "已开始".
        const startWarnings = Array.isArray(started?.warnings)
          ? started.warnings.filter((text) => typeof text === "string" && text.trim())
          : [];
        state.initReason = startWarnings.join(" ");
        showToast(startWarnings.length ? startWarnings.join(" ") : "初始化已开始");
        renderAll();
        scheduleInitStatusRefresh(INIT_STATUS_START_POLL_MS);
      } catch (error) {
        const code = error?.details?.error || error?.details?.reason;
        state.initReason = describeInitReason(code) || error?.message || "初始化没能启动，请稍后重试。";
        state.initBusy = false;
        renderAll();
      }
    }

    async function handleDesktopCancelInitClick(button) {
      if (button instanceof HTMLButtonElement) {
        button.disabled = true;
        button.textContent = "正在取消…";
      }
      try {
        await requestJsonStrict(ENDPOINTS.cancelInit, { method: "POST", timeoutMs: 15000 });
        showToast("已发送取消请求，正在安全结束当前步骤");
        scheduleInitStatusRefresh(300);
      } catch (error) {
        if (error?.status !== 409) {
          state.initReason = error?.details?.detail || error?.message || "取消请求失败。";
          renderAll();
        } else {
          scheduleInitStatusRefresh(300);
        }
      } finally {
        if (button instanceof HTMLButtonElement && button.isConnected) {
          button.textContent = "取消初始化";
          button.disabled = false;
        }
      }
    }

    // Legacy init status remains readable while the NewsClaw bootstrap and
    // runtime contracts are phased in. It must not expose a clickable
    // "rebuild" action: /api/init is a video-era workflow and NewsClaw's
    // /api/news/bootstrap intentionally has no destructive force mode.
    function renderSettingsReinitStatus() {
      const status = state.initStatus;
      const badge = $("#reinitStateBadge");
      if (badge) {
        if (!status) {
          badge.hidden = true;
        } else {
          badge.hidden = false;
          if (status.running) {
            badge.textContent = "正在重新初始化";
            badge.dataset.tone = "running";
          } else if (status.initialized) {
            badge.textContent = status.partial_success ? "初始化部分完成" : "已初始化";
            badge.dataset.tone = "";
          } else {
            badge.textContent = "尚未初始化";
            badge.dataset.tone = "";
          }
        }
      }
      const statusEl = $("#reinitStatus");
      const btn = $("#reinitBtn");
      if (!status) {
        if (statusEl) statusEl.textContent = "读取初始化状态中…";
        if (btn) btn.disabled = false;
        return;
      }
      if (status.running) {
        if (statusEl) {
          statusEl.textContent = `初始化进行中（阶段 ${status.current_stage || "?"}/${status.total_stages || 4}）。请等待本轮完成后再重新初始化。`;
        }
        if (btn) btn.disabled = true;
        return;
      }
      if (btn) btn.disabled = false;
      if (statusEl) {
        statusEl.textContent = status.initialized
          ? "系统已初始化。重新初始化会重新拉取数据并重建画像，现有事件与收藏保留。"
          : "系统尚未初始化完成；正常流程请到「推荐」页点击开始初始化。";
      }
    }

    function openPanel(id) {
      const panel = document.getElementById(id);
      if (!panel) return;
      if (panel._closeTimer) {
        window.clearTimeout(panel._closeTimer);
        panel._closeTimer = null;
      }
      if (panel._closeHandler) {
        panel.removeEventListener("animationend", panel._closeHandler);
        panel._closeHandler = null;
      }
      panel.classList.remove("is-closing");
      panel.classList.add("is-open");
    }

    function closePanel(id) {
      const panel = document.getElementById(id);
      if (!panel || !panel.classList.contains("is-open") || panel.classList.contains("is-closing")) return;

      const finishClose = () => {
        if (panel._closeTimer) {
          window.clearTimeout(panel._closeTimer);
          panel._closeTimer = null;
        }
        if (panel._closeHandler) {
          panel.removeEventListener("animationend", panel._closeHandler);
          panel._closeHandler = null;
        }
        panel.classList.remove("is-open", "is-closing", "from-mobile-menu");
        if (id === "messagesDrawer") {
          state.messageListSnapshot = null;
          state.messageListDomLocked = false;
        }
      };

      panel._closeHandler = finishClose;
      panel.classList.add("is-closing");
      panel.addEventListener("animationend", finishClose, { once: true });
      panel._closeTimer = window.setTimeout(finishClose, 220);
    }

    const MAIN_PAGE_IDS = ["homePage", "contentLibraryPage", "profilePage", "chatPage", "settingsPage"];

    function showMainPage(pageId) {
      MAIN_PAGE_IDS.forEach((id) => {
        const page = document.getElementById(id);
        if (!page) return;
        if (id === pageId) page.removeAttribute("hidden");
        else page.setAttribute("hidden", "");
      });
      document.body.classList.toggle("profile-page-open", pageId === "profilePage");
      document.body.classList.toggle("chat-page-open", pageId === "chatPage");
      document.body.classList.toggle("content-page-open", pageId !== "homePage");
      const navPageMap = {
        contentLibraryBtn: "contentLibraryPage",
        profileBtn: "profilePage",
        chatBtn: "chatPage",
        settingsBtn: "settingsPage",
      };
      Object.entries(navPageMap).forEach(([buttonId, targetPage]) => {
        const button = document.getElementById(buttonId);
        if (!button) return;
        if (pageId === targetPage) button.setAttribute("aria-current", "page");
        else button.removeAttribute("aria-current");
      });
      if (pageId !== "homePage") {
        document.getElementById("homeBtn")?.removeAttribute("aria-current");
        document.getElementById("newMaterialsBtn")?.removeAttribute("aria-current");
      }
      if (pageId !== "homePage") {
        document.body.classList.remove("event-workspace-open", "new-materials-open");
        eventWorkspaceController?.deactivate();
      }
    }

    function syncTopbarHeight() {
      const topbar = document.querySelector(".topbar");
      if (!topbar) return;
      document.documentElement.style.setProperty("--topbar-height", `${Math.ceil(topbar.getBoundingClientRect().height)}px`);
    }

    function syncHomeSearch() {
      const eventsMode = state.homeMode === "events";
      const desktopInput = $("#searchInput");
      const mobileInput = $("#mobileSearchInput");
      const value = eventsMode
        ? String(eventWorkspaceController?.snapshot?.()?.query || "")
        : state.query;
      const placeholder = eventsMode ? "筛选当前已加载的事件" : "筛选当前已加载的新材料";
      [desktopInput, mobileInput].forEach((input) => {
        if (!input) return;
        input.value = value;
        input.placeholder = placeholder;
      });
    }

    function setHomeMode(value, { persist = true } = {}) {
      const mode = value === "materials" ? "materials" : "events";
      state.homeMode = mode;
      if (persist) storageSet(HOME_MODE_STORAGE_KEY, mode);
      const eventWorkspace = document.getElementById("eventWorkspace");
      const recommendationWorkspace = document.getElementById("recommendationWorkspace");
      if (eventWorkspace) eventWorkspace.hidden = mode !== "events";
      if (recommendationWorkspace) recommendationWorkspace.hidden = mode !== "materials";
      const homeVisible = !document.getElementById("homePage")?.hidden;
      document.body.classList.toggle("event-workspace-open", mode === "events" && homeVisible);
      document.body.classList.toggle("new-materials-open", mode === "materials" && homeVisible);
      document.getElementById("homeBtn")?.setAttribute("aria-current", mode === "events" ? "page" : "false");
      document.getElementById("newMaterialsBtn")?.setAttribute("aria-current", mode === "materials" ? "page" : "false");
      syncHomeSearch();
      if (mode === "events" && homeVisible) eventWorkspaceController?.activate();
      else eventWorkspaceController?.deactivate();
    }

    function openHomePage(mode = "events") {
      leaveDesktopContentLibrary();
      clearDesktopContentLibraryRoute();
      leavePersonalMemory();
      clearPersonalMemoryRoute();
      showMainPage("homePage");
      setHomeMode(mode);
      closeMobileMenu();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openNewMaterialsPage() {
      openHomePage("materials");
    }

    function setNewsClawProductMode(enabled) {
      const newsclawMode = Boolean(enabled);
      const previousMode = state.newsclawMode;
      state.newsclawMode = newsclawMode;
      if (previousMode !== newsclawMode) state.newsclawModeGeneration += 1;
      document.body.classList.toggle("newsclaw-product", newsclawMode);
      const personalMount = document.getElementById("personalMemoryMount");
      const legacyMount = document.getElementById("legacyProfileMount");
      if (personalMount) personalMount.hidden = !newsclawMode;
      if (legacyMount) {
        legacyMount.hidden = newsclawMode;
        legacyMount.setAttribute("aria-hidden", String(newsclawMode));
      }
      const page = document.getElementById("profilePage");
      if (page) {
        if (newsclawMode) {
          page.setAttribute("aria-labelledby", "memoryPageTitle");
          page.removeAttribute("aria-label");
        } else {
          page.removeAttribute("aria-labelledby");
          page.setAttribute("aria-label", "我的画像");
        }
      }
      const navLabel = document.getElementById("profileNavLabel");
      if (navLabel) navLabel.textContent = newsclawMode ? "个人记忆" : "我的画像";
      const mobileNavLabel = document.getElementById("mobileProfileNavLabel");
      if (mobileNavLabel) mobileNavLabel.textContent = newsclawMode ? "个人记忆" : "我的画像";
      const mobileHint = document.getElementById("mobileProfileHint");
      if (mobileHint) mobileHint.textContent = newsclawMode
        ? "查看已确认信息、当前研究和待确认内容"
        : "查看完整画像和近期记忆";
      const mobileMemory = document.getElementById("mobilePersonalMemorySummary");
      const mobileLegacy = document.getElementById("mobileLegacyProfileSummary");
      if (mobileMemory) mobileMemory.hidden = !newsclawMode;
      if (mobileLegacy) mobileLegacy.hidden = newsclawMode;
      if (newsclawMode) renderPersonalMemory();
      else renderProfileDetails();
      if (!newsclawMode) {
        state.webMonitorNotificationGeneration += 1;
        state.webMonitors.refreshGeneration += 1;
        state.webMonitors.items = null;
        state.webMonitors.runtime = null;
        state.webMonitors.loading = false;
        state.webMonitors.error = "";
        state.webMonitors.refreshRequested = false;
        state.webMonitors.refreshPromise = null;
        state.webMonitors.histories.clear();
        for (const [monitorId, generation] of state.webMonitors.historyGenerations) {
          state.webMonitors.historyGenerations.set(monitorId, generation + 1);
        }
        state.webMonitors.busyIds.clear();
        state.webMonitorNotificationActions.clear();
        state.messages = state.messages.filter(
          (item) => messageType(item) !== "web-monitor-notification"
        );
        if (state.messageListSnapshot) {
          state.messageListSnapshot = state.messageListSnapshot.filter(
            (item) => messageType(item) !== "web-monitor-notification"
          );
        }
        syncMessageCount();
        if (isMessagesDrawerOpen()) renderMessages();
        renderWebMonitors();
      }
      const monitoringTab = document.getElementById("contentLibraryMonitoringTab");
      if (monitoringTab) monitoringTab.hidden = !newsclawMode;
      if (!newsclawMode && desktopContentLibraryTab === "monitoring") {
        desktopContentLibraryTab = "watchLater";
        if (desktopContentLibraryVisible) {
          activateDesktopContentLibraryTab("watchLater", { forceLoad: true });
        }
      }
      if (newsclawMode && previousMode !== true) {
        void refreshWebMonitors();
        void refreshWebMonitorNotifications();
      }
    }

    function openLegacyProfilePage() {
      leaveDesktopContentLibrary();
      clearDesktopContentLibraryRoute();
      leavePersonalMemory();
      clearPersonalMemoryRoute();
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("profilePage");
      renderProfileDetails();
      void refreshProfile().catch(() => {});
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openProfilePage(tab = state.personalMemory.activeTab) {
      if (state.newsclawMode === null) {
        void requestJson(ENDPOINTS.health).then((snapshot) => {
          const service = String(snapshot?.service || "");
          if (service) setNewsClawProductMode(service === "newsclaw-api");
          if (state.newsclawMode === true) openPersonalMemoryPage(tab);
          else openLegacyProfilePage();
        }).catch(() => openLegacyProfilePage());
        return;
      }
      if (state.newsclawMode === false) {
        openLegacyProfilePage();
        return;
      }
      leaveDesktopContentLibrary();
      openPersonalMemoryPage(tab);
    }

    function openChatPage() {
      leaveDesktopContentLibrary();
      clearDesktopContentLibraryRoute();
      leavePersonalMemory();
      clearPersonalMemoryRoute();
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      const forceBottom = !hasOpenedDialogueChatPage;
      showMainPage("chatPage");
      renderChat({ forceBottom });
      hasOpenedDialogueChatPage = true;
      scheduleDialogueConfirmationRefresh();
      const input = document.getElementById("chatInput");
      window.scrollTo({ top: 0, behavior: "smooth" });
      window.setTimeout(() => input?.focus(), 100);
    }

    function openSettingsPage(panel = "models") {
      leaveDesktopContentLibrary();
      clearDesktopContentLibraryRoute();
      leavePersonalMemory();
      clearPersonalMemoryRoute();
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((drawer) => closePanel(drawer.id));
      setActiveSettingsPanel(panel || "models");
      showMainPage("settingsPage");
      window.scrollTo({ top: 0, behavior: "smooth" });
      renderSettingsReinitStatus();
      if (!state.degraded) {
        void renderSourcesStatus();
        void renderSourceCredentials();
      }
      void lanAuthControl?.reload();
      void bootAutostartControl?.reload();
    }

    // ── Saved pages: 稍后读 (watch-later) & 收藏 (favorites) ──────
    // The two are independent backend collections sharing one list UI.

    const SAVED_SYNC_PRESENTATION = {
      pending: ["待同步", "neutral", false], syncing: ["同步中", "info", false],
      synced: ["已同步", "success", false], already_synced: ["已同步", "success", false],
      login_required: ["需要登录", "warning", true], unsupported: ["仅本地保存", "neutral", false],
      rate_limited: ["同步失败", "error", true], extension_required: ["需要连接插件", "warning", true],
      failed: ["同步失败", "error", true]
    };
    function safeSavedText(value, maxLength = 240) {
      return String(value || "").replace(/[\p{C}\p{Zl}\p{Zp}]/gu, "").trim().slice(0, maxLength);
    }

    function desktopSavedItem(itemOrBvid = {}) {
      const item = typeof itemOrBvid === "object" && itemOrBvid ? itemOrBvid : { bvid: itemOrBvid };
      const canonical = window.NewsClawSavedSync.normalizeSavedItem(item);
      const localOnly = String(canonical.source_platform || "").toLowerCase() === "news";
      return {
        ...item,
        item_key: safeSavedText(canonical.item_key, 2048),
        source_platform: safeSavedText(canonical.source_platform, 64),
        content_id: safeSavedText(canonical.content_id, 2048),
        content_url: safeSavedText(canonical.content_url, 2048),
        content_type: safeSavedText(canonical.content_type, 128),
        title: safeSavedText(item.title || canonical.content_id),
        author_name: safeSavedText(item.author_name || item.up_name),
        cover_url: safeSavedText(item.cover_url, 2048),
        sync_status: localOnly
          ? "unsupported"
          : (SAVED_SYNC_PRESENTATION[item.sync_status] ? item.sync_status : (item.sync_status ? "failed" : "")),
        sync_task_id: localOnly ? "" : safeSavedText(item.sync_task_id, 96),
        requested_action: localOnly ? "" : safeSavedText(item.requested_action, 96),
        resolved_action: localOnly ? "" : safeSavedText(item.resolved_action, 96),
        resolved_target: localOnly ? "" : safeSavedText(item.resolved_target),
        error_code: localOnly ? "local_only_source" : safeSavedText(item.error_code, 96),
        error_message: localOnly
          ? "新闻来源只保存在本机，不会修改媒体网站账号。"
          : safeSavedText(item.error_message)
      };
    }

    const desktopSavedApi = window.NewsClawSavedSync.createStrictSavedApi(requestJsonStrict);
    const desktopSavedMutations = window.NewsClawSavedSync.createSavedMutationRegistry();
    const desktopSavedChangeEpochs = new Map();
    const desktopSavedListStates = {
      watch_later: window.NewsClawSavedSync.createRetainedSavedListState(),
      favorite: window.NewsClawSavedSync.createRetainedSavedListState()
    };
    const desktopSavedBadgeSyncGenerations = { watch_later: 0, favorite: 0 };
    const desktopSyncingKeys = {
      watch_later: window.NewsClawSavedSync.createSavedSubmissionFence(),
      favorite: window.NewsClawSavedSync.createSavedSubmissionFence()
    };
    const desktopSavedPendingFocus = { watch_later: null, favorite: null };
    function createDesktopSavedTaskRuntime() {
      const tracker = window.NewsClawSavedSync.createDurableTaskTracker({
        poll: (taskId) => desktopSavedApi.pollTask(taskId),
        isVisible: () => !document.hidden
      });
      return {
        tracker,
        coordinator: window.NewsClawSavedSync.createSavedTaskCoordinator({
          tracker,
          fetchTask: (taskId) => desktopSavedApi.pollTask(taskId)
        })
      };
    }
    const desktopSavedTaskRuntimes = {
      watch_later: createDesktopSavedTaskRuntime(),
      favorite: createDesktopSavedTaskRuntime()
    };
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        pauseDesktopBackendSession();
        return;
      }
      for (const runtime of Object.values(desktopSavedTaskRuntimes)) runtime.coordinator.resumeAll();
      restartDesktopFailedRecoveries();
      if (initRefreshPending || state.initStatus?.running) {
        scheduleInitStatusRefresh(0);
      }
      void startDesktopBackendSession();
    });
    window.addEventListener("pagehide", () => {
      dialogueCardActionAbortController.abort();
      pauseDesktopBackendSession();
      for (const runtime of Object.values(desktopSavedTaskRuntimes)) runtime.coordinator.dispose();
    }, { once: true });
    function saveDesktopItem(listKind, item) {
      return desktopSavedApi.save(listKind, desktopSavedItem(item));
    }
    function removeDesktopSavedItem(listKind, itemKey) {
      return desktopSavedApi.remove(listKind, itemKey);
    }
    function savedStatus(listKind, itemOrBvid) {
      const itemKey = desktopSavedItem(itemOrBvid).item_key;
      return desktopSavedApi.status(listKind, itemKey);
    }
    function watchLaterStatus(itemOrBvid) { return savedStatus("watch_later", itemOrBvid); }
    function favoriteStatus(itemOrBvid) { return savedStatus("favorite", itemOrBvid); }
    function fetchDesktopSaved(listKind) { return desktopSavedApi.list(listKind); }
    function syncDesktopSaved(listKind, itemKeys) {
      return desktopSavedApi.sync(listKind, itemKeys);
    }

    function normalizeDesktopSavedKind(value) {
      const kind = String(value || "").trim().toLowerCase();
      return kind === "favorite" || kind === "watch_later" ? kind : "";
    }

    function desktopSavedChangeKey(listKind, itemKey) {
      return `${listKind}:${itemKey}`;
    }

    function bumpDesktopSavedChangeEpoch(listKind, itemKey) {
      const key = desktopSavedChangeKey(listKind, itemKey);
      const next = (desktopSavedChangeEpochs.get(key) || 0) + 1;
      desktopSavedChangeEpochs.set(key, next);
      return next;
    }

    function reconcileSavedChangeAfterBusy(listKind, itemKey, attempt = 0) {
      if (desktopSavedMutations.isBusy(listKind, itemKey)) {
        if (attempt < 20) {
          window.setTimeout(() => reconcileSavedChangeAfterBusy(listKind, itemKey, attempt + 1), 150);
        } else {
          void (listKind === "favorite" ? syncFavoriteButtons() : syncWatchLaterButtons());
        }
        return;
      }
      const expectedEpoch = desktopSavedChangeEpochs.get(desktopSavedChangeKey(listKind, itemKey)) || 0;
      void fetchDesktopSaved(listKind).then((data) => {
        if (desktopSavedMutations.isBusy(listKind, itemKey)) {
          reconcileSavedChangeAfterBusy(listKind, itemKey);
          return;
        }
        const currentEpoch = desktopSavedChangeEpochs.get(desktopSavedChangeKey(listKind, itemKey)) || 0;
        if (currentEpoch !== expectedEpoch) return;
        const savedKeys = new Set((data?.items || []).map((item) => desktopSavedItem(item).item_key));
        applySavedChangeAcrossSurfaces({
          type: "saved-changed",
          kind: listKind,
          item_key: itemKey,
          saved: savedKeys.has(itemKey),
        });
      }).catch(() => {});
    }

    function applySavedChangeAcrossSurfaces(payload) {
      const listKind = normalizeDesktopSavedKind(payload?.kind);
      const itemKey = safeSavedText(payload?.item_key, 2048);
      if (!listKind || !itemKey || typeof payload?.saved !== "boolean") return;
      bumpDesktopSavedChangeEpoch(listKind, itemKey);
      if (desktopSavedMutations.isBusy(listKind, itemKey)) {
        reconcileSavedChangeAfterBusy(listKind, itemKey);
        return;
      }

      const saved = payload.saved;
      desktopSavedMutations.setSaved(listKind, itemKey, saved);
      for (const item of state.videos) {
        if (desktopSavedItem(item).item_key === itemKey) syncSavedControlsForRecommendation(item);
      }

      const delightField = listKind === "favorite" ? "favorite" : "watchLater";
      for (const delight of state.delights || []) {
        if (desktopSavedItem(delight).item_key !== itemKey) continue;
        _delightStatusCache.set(itemKey, {
          ...(_delightStatusCache.get(itemKey) || {}),
          [delightField]: saved,
        });
      }
      if (state.delight && desktopSavedItem(state.delight).item_key === itemKey) {
        _syncDelightStatusButtons(itemKey);
      }

      const visibleList = listKind === "favorite" ? "favorites" : "watchLater";
      if (desktopContentLibraryVisible && desktopContentLibraryTab === visibleList) {
        void (listKind === "favorite" ? refreshFavorites() : refreshWatchLater());
      } else {
        void (listKind === "favorite" ? syncFavoriteButtons() : syncWatchLaterButtons());
      }
      if (desktopContentLibraryVisible && desktopContentLibraryTab === "history") {
        void refreshContentHistory(true);
      }
    }

    function publishSavedChange(listKind, itemKey, saved) {
      const payload = {
        type: "saved-changed",
        kind: normalizeDesktopSavedKind(listKind),
        item_key: safeSavedText(itemKey, 2048),
        saved: Boolean(saved),
      };
      if (!payload.kind || !payload.item_key) return;
      applySavedChangeAcrossSurfaces(payload);
      recommendationSyncChannel?.postMessage(payload);
    }

    function summarizeDesktopSavedTask(items) {
      const groups = new Map();
      for (const item of items) {
        const slug = item.item_key.split(":", 1)[0] || "unknown";
        const group = groups.get(slug) || [0, 0];
        group[1] += 1;
        if (["synced", "already_synced"].includes(item.status)) group[0] += 1;
        groups.set(slug, group);
      }
      return Array.from(groups, ([slug, [success, total]]) => `${platformName(slug)} ${success}/${total}`).join(" · ");
    }

    function savedSyncEligible(item, listKind = "") {
      return window.NewsClawSavedSync.isSavedSyncEligibleStatus(
        item.sync_status,
        item.error_code,
        item.sync_task_id
      )
        && !desktopSavedTaskRuntimes[listKind]?.coordinator.owns(item.item_key);
    }

    function updateSavedBadge(badgeId, total) {
      const badge = document.getElementById(badgeId);
      if (!badge) return;
      const n = Number(total) || 0;
      if (n > 0) {
        badge.textContent = n > 99 ? "99+" : String(n);
        badge.removeAttribute("hidden");
      } else {
        badge.textContent = "";
        badge.setAttribute("hidden", "");
      }
    }

    const SAVED_IMAGE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>';

    function bindSavedCoverFallback(cover) {
      if (!cover) return;
      // 无图新闻故意使用标签信息板，不是图片加载失败。
      if (cover.querySelector(".tag-fingerprint")) return;
      const image = cover.querySelector("img");
      let fallbackShown = false;
      const showFallback = () => {
        if (fallbackShown || !cover.isConnected) return;
        fallbackShown = true;
        cover.classList.add("is-fallback");
        const fallback = document.createElement("span");
        fallback.className = "saved-cover-fallback";
        fallback.setAttribute("aria-hidden", "true");
        fallback.innerHTML = SAVED_IMAGE_ICON;
        if (image?.isConnected) image.replaceWith(fallback);
        else cover.prepend(fallback);
      };
      if (!image) {
        showFallback();
        return;
      }
      image.addEventListener("error", showFallback, { once: true });
      // Cached image failures can predate listener registration.
      if (image.complete && image.naturalWidth === 0) queueMicrotask(showFallback);
    }

    function renderSavedList(listKind, listId, emptyId, items, reload) {
      const grid = document.getElementById(listId);
      const empty = document.getElementById(emptyId);
      if (!grid) return;
      const focusRoot = grid.closest(".saved-page") || grid;
      const focusToken = window.NewsClawSavedSync.captureSavedFocus(focusRoot)
        || desktopSavedPendingFocus[listKind];
      const rows = Array.isArray(items) ? items : [];
      if (!rows.length) {
        grid.replaceChildren();
        if (empty) empty.removeAttribute("hidden");
        if (window.NewsClawSavedSync.restoreSavedFocus(focusRoot, focusToken)) {
          desktopSavedPendingFocus[listKind] = null;
        }
        return;
      }
      if (empty) empty.setAttribute("hidden", "");
      grid.replaceChildren(...rows.map((item) => {
        item = desktopSavedItem(item);
        if (desktopSyncingKeys[listKind].has(item.item_key)
          || desktopSavedTaskRuntimes[listKind].coordinator.owns(item.item_key)) {
          item.sync_status = "syncing";
        }
        const syncPresentation = window.NewsClawSavedSync.getSavedSyncPresentation(item);
        const card = document.createElement("article");
        card.className = "video-card saved-card";
        card.dataset.itemKey = item.item_key;
        const url = contentUrl(item);
        const savedCoverClass = recommendationCoverClass(item);
        const coverContent = `
            ${recommendationMediaHtml(item)}
            <span class="platform" data-platform="${escapeHtml(item.source_platform || item.platform || "news")}">${escapeHtml(recommendationMediaLabel(item) || platformName(item.source_platform || item.platform || "news"))}</span>
          `;
        card.innerHTML = `
          ${url
            ? `<a class="cover${savedCoverClass}" data-platform="${escapeHtml(item.source_platform || item.platform || "news")}" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" aria-label="打开 ${escapeHtml(item.title || item.content_id)}">${coverContent}</a>`
            : `<button class="cover${savedCoverClass}" data-platform="${escapeHtml(item.source_platform || item.platform || "news")}" type="button" aria-label="打开 ${escapeHtml(item.title || item.content_id)}">${coverContent}</button>`}
          <div>
            <p class="video-title">${escapeHtml(item.title || item.content_id)}</p>
            <p class="video-meta">${escapeHtml(item.author_name || "")}</p>
            <p class="saved-sync-line"><span class="saved-sync-chip" data-tone="${escapeHtml(syncPresentation.tone)}">${escapeHtml(syncPresentation.label)}</span><span>${escapeHtml(syncPresentation.detail)}</span></p>
          </div>
${savedCardFeedbackBarHtml(listKind)}
          <div class="card-actions saved-card-actions">
            ${syncPresentation.actionable || syncPresentation.busy ? `<button class="small-btn saved-sync-one" data-saved-action="sync" type="button" aria-disabled="${syncPresentation.busy}" aria-label="${escapeHtml(syncPresentation.busy ? `${syncPresentation.label}，请稍候` : syncPresentation.actionLabel)}" ${syncPresentation.busy ? "disabled" : ""}>${escapeHtml(syncPresentation.actionLabel)}</button>` : ""}
            <button class="small-btn saved-remove" data-saved-action="remove" type="button" title="只从 NewsClaw 本地移除">移除</button>
          </div>`;
        const cover = card.querySelector(".cover");
        bindSavedCoverFallback(cover);
        cover.addEventListener("click", () => {
          if (url) trackRecommendationClick(item);
        });
        cover.addEventListener("auxclick", (event) => {
          if (url && event.button === 1) trackRecommendationClick(item);
        });
        card.querySelector(".saved-sync-one")?.addEventListener("click", (e) => {
          desktopSavedPendingFocus[listKind] = window.NewsClawSavedSync.captureSavedFocus(focusRoot, e.currentTarget);
          void runDesktopSavedSync(listKind, [item], e.currentTarget, reload);
        });
        card.querySelector(".saved-remove").addEventListener("click", async (e) => {
          const btn = e.currentTarget;
          desktopSavedPendingFocus[listKind] = window.NewsClawSavedSync.captureSavedFocus(focusRoot, btn);
          btn.disabled = true;
          try {
            await removeDesktopSavedItem(listKind, item.item_key);
            publishSavedChange(listKind, item.item_key, false);
            await reload();
          } catch (error) {
            btn.disabled = false;
            const status = document.getElementById(listKind === "watch_later" ? "watchLaterSyncStatus" : "favoritesSyncStatus");
            if (status) { status.setAttribute("role", "alert"); status.textContent = error?.message || "本地移除失败，请重试。"; }
          }
        });
        wireSavedCardFeedback(card, item, listKind);
        return card;
      }));
      if (window.NewsClawSavedSync.restoreSavedFocus(focusRoot, focusToken)) {
        desktopSavedPendingFocus[listKind] = null;
      }
    }

    async function runDesktopSavedSync(listKind, selected, activeButton, reload, confirmBatch = false) {
      const coordinator = desktopSavedTaskRuntimes[listKind].coordinator;
      const eligible = selected.filter((item) => savedSyncEligible(item, listKind)
        && !desktopSyncingKeys[listKind].has(item.item_key));
      if (!eligible.length || activeButton?.disabled) return;
      const platforms = Array.from(new Set(eligible.map((item) => platformName(item.source_platform))));
      if (confirmBatch && !window.confirm(`将同步 ${eligible.length} 项到 ${platforms.join("、")}，继续吗？`)) return;
      const eligibleKeys = eligible.map((item) => item.item_key);
      if (!desktopSyncingKeys[listKind].claim(eligibleKeys)) return;
      const status = document.getElementById(listKind === "watch_later" ? "watchLaterSyncStatus" : "favoritesSyncStatus");
      let submitted = false;
      if (activeButton) {
        const focusRoot = activeButton.closest(".saved-page") || activeButton.parentElement;
        desktopSavedPendingFocus[listKind] = window.NewsClawSavedSync.captureSavedFocus(focusRoot, activeButton)
          || { kind: "list", action: "sync-all" };
        activeButton.disabled = true;
        activeButton.setAttribute("aria-disabled", "true");
        activeButton.setAttribute("aria-busy", "true");
        activeButton.textContent = "同步中…";
      }
      if (status) { status.removeAttribute("role"); status.textContent = `正在同步 ${eligible.length} 项…`; }
      try {
        const task = await syncDesktopSaved(listKind, eligibleKeys);
        const taskId = safeSavedText(task?.task_id, 64);
        if (!taskId) throw new Error("同步任务缺少 task_id，请重试。");
        coordinator.track(task, eligibleKeys, {
          onProgress: () => { if (status) status.textContent = `正在同步 ${eligible.length} 项…`; },
          onBackground: () => { if (status) status.textContent = "仍在后台同步；可切换页面，返回后会继续更新。"; },
          onPollError: () => { if (status) status.textContent = "仍在后台同步；连接恢复后会继续查询。"; },
          onTerminal: (terminalTask) => {
            if (status) status.textContent = summarizeDesktopSavedTask(terminalTask.items) || "同步已完成";
            void reload();
          }
        });
        submitted = true;
        if (status) status.textContent = `同步任务已提交 · ${eligible.length} 项`;
      } catch (error) {
        if (status) { status.setAttribute("role", "alert"); status.textContent = error?.message || "同步失败，请重试。"; }
      } finally {
        desktopSyncingKeys[listKind].release(eligibleKeys);
        if (!submitted && activeButton) {
          activeButton.disabled = false;
          activeButton.setAttribute("aria-disabled", "false");
          activeButton.removeAttribute("aria-busy");
        }
        await reload();
      }
    }

    function bindDesktopSavedBatch(listKind, items, reload) {
      const id = listKind === "watch_later" ? "watchLaterSyncAll" : "favoritesSyncAll";
      const button = document.getElementById(id);
      if (!button) return;
      const count = items.filter((item) => savedSyncEligible(item, listKind)
        && !desktopSyncingKeys[listKind].has(item.item_key)).length;
      button.textContent = `同步未同步内容（${count}）`;
      window.NewsClawSavedSync.updateSavedBatchButtonState(button, count);
      button.onclick = () => runDesktopSavedSync(listKind, items, button, reload, true);
    }

    function showDesktopSavedLoadError(listKind, status, state, reload) {
      if (!status) return;
      status.setAttribute("role", "alert");
      status.dataset.loadError = "true";
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "small-btn saved-load-retry";
      retry.dataset.savedListAction = "retry";
      retry.textContent = "重试加载";
      retry.addEventListener("click", (event) => {
        const focusRoot = status.closest(".saved-page") || status.parentElement;
        desktopSavedPendingFocus[listKind] = window.NewsClawSavedSync.captureSavedFocus(
          focusRoot,
          event.currentTarget,
        ) || { kind: "list", action: "retry" };
        void reload();
      });
      status.replaceChildren(document.createTextNode(`${state.snapshot().error} `), retry);
    }

    function desktopRecoveredTaskCallbacks(listKind, reload) {
      const status = document.getElementById(
        listKind === "watch_later" ? "watchLaterSyncStatus" : "favoritesSyncStatus",
      );
      return {
        onProgress: () => { if (status) status.textContent = "正在同步已恢复的任务…"; },
        onBackground: () => {
          if (status) status.textContent = "仍在后台同步；可切换页面，返回后会继续更新。";
        },
        onPollError: () => {
          if (status) status.textContent = "同步状态查询超时；连接恢复后会继续查询。";
        },
        onTerminal: (task) => {
          if (status) status.textContent = summarizeDesktopSavedTask(task.items) || "同步已完成";
          void reload();
        },
      };
    }

    async function refreshWatchLater() {
      const generation = ++desktopSavedBadgeSyncGenerations.watch_later;
      const isCurrent = () => generation === desktopSavedBadgeSyncGenerations.watch_later;
      const retained = desktopSavedListStates.watch_later;
      try {
        const data = await fetchDesktopSaved("watch_later");
        if (!isCurrent()) return;
        retained.commit({ items: (data?.items || []).map(desktopSavedItem), total: data?.total });
        await desktopSavedTaskRuntimes.watch_later.coordinator.recover(
          retained.snapshot().items,
          desktopRecoveredTaskCallbacks("watch_later", refreshWatchLater),
        );
        if (!isCurrent()) return;
        const status = document.getElementById("watchLaterSyncStatus");
        if (status?.dataset.loadError === "true") { status.replaceChildren(); status.removeAttribute("role"); delete status.dataset.loadError; }
      } catch (error) {
        if (!isCurrent()) return;
        retained.fail(error);
        showDesktopSavedLoadError("watch_later", document.getElementById("watchLaterSyncStatus"), retained, refreshWatchLater);
      }
      if (!isCurrent()) return;
      const { items, total } = retained.snapshot();
      renderSavedList("watch_later", "watchLaterList", "watchLaterEmpty", items, refreshWatchLater);
      bindDesktopSavedBatch("watch_later", items, refreshWatchLater);
      updateSavedBadge("watchLaterCountBadge", total);
    }

    async function refreshFavorites() {
      const generation = ++desktopSavedBadgeSyncGenerations.favorite;
      const isCurrent = () => generation === desktopSavedBadgeSyncGenerations.favorite;
      const retained = desktopSavedListStates.favorite;
      try {
        const data = await fetchDesktopSaved("favorite");
        if (!isCurrent()) return;
        retained.commit({ items: (data?.items || []).map(desktopSavedItem), total: data?.total });
        await desktopSavedTaskRuntimes.favorite.coordinator.recover(
          retained.snapshot().items,
          desktopRecoveredTaskCallbacks("favorite", refreshFavorites),
        );
        if (!isCurrent()) return;
        const status = document.getElementById("favoritesSyncStatus");
        if (status?.dataset.loadError === "true") { status.replaceChildren(); status.removeAttribute("role"); delete status.dataset.loadError; }
      } catch (error) {
        if (!isCurrent()) return;
        retained.fail(error);
        showDesktopSavedLoadError("favorite", document.getElementById("favoritesSyncStatus"), retained, refreshFavorites);
      }
      if (!isCurrent()) return;
      const { items, total } = retained.snapshot();
      renderSavedList("favorite", "favoritesList", "favoritesEmpty", items, refreshFavorites);
      bindDesktopSavedBatch("favorite", items, refreshFavorites);
      updateSavedBadge("favoritesCountBadge", total);
    }

    function reconcileRecommendationSavedRegistry(listKind, savedKeys) {
      for (const item of state.videos) {
        const itemKey = desktopSavedItem(item).item_key;
        if (!itemKey || desktopSavedMutations.isBusy(listKind, itemKey)) continue;
        desktopSavedMutations.setSaved(listKind, itemKey, savedKeys.has(itemKey));
        syncSavedControlsForRecommendation(item);
      }
    }

    // Re-sync the pressed state + count badge for all visible ☆/♥ toggles.
    function syncWatchLaterButtons() {
      const generation = desktopSavedBadgeSyncGenerations.watch_later;
      return fetchDesktopSaved("watch_later").then((data) => {
        if (generation !== desktopSavedBadgeSyncGenerations.watch_later) return;
        const saved = new Set((data?.items || []).map((it) => desktopSavedItem(it).item_key));
        document.querySelectorAll('.video-card [data-action="watch-later"]').forEach((btn) => {
          const card = btn.closest(".video-card");
          const item = state.videos.find((row) => String(row.bvid || row.content_id) === card?.dataset?.bvid);
          if (!item) return;
          const on = saved.has(desktopSavedItem(item).item_key);
          btn.setAttribute("aria-pressed", on ? "true" : "false");
        });
        reconcileRecommendationSavedRegistry("watch_later", saved);
        updateSavedBadge("watchLaterCountBadge", data?.total);
      }).catch(() => {});
    }

    function syncFavoriteButtons() {
      const generation = desktopSavedBadgeSyncGenerations.favorite;
      return fetchDesktopSaved("favorite").then((data) => {
        if (generation !== desktopSavedBadgeSyncGenerations.favorite) return;
        const saved = new Set((data?.items || []).map((it) => desktopSavedItem(it).item_key));
        document.querySelectorAll('.video-card [data-action="favorite"]').forEach((btn) => {
          const card = btn.closest(".video-card");
          const item = state.videos.find((row) => String(row.bvid || row.content_id) === card?.dataset?.bvid);
          if (!item) return;
          const on = saved.has(desktopSavedItem(item).item_key);
          btn.setAttribute("aria-pressed", on ? "true" : "false");
        });
        reconcileRecommendationSavedRegistry("favorite", saved);
        updateSavedBadge("favoritesCountBadge", data?.total);
      }).catch(() => {});
    }

    const TRACKED_OBSERVATION_LIMIT = 100;
    const TRACKED_OBSERVATION_TIMELINE_LIMIT = 50;
    const trackedObservationPageState = {
      items: [],
      total: 0,
      activeCount: 0,
      archivedCount: 0,
      unreadCount: 0,
      loading: false,
      loadingMore: false,
      error: "",
    };
    const trackedObservationTimelineState = new Map();
    const trackedObservationExpanded = new Set();
    const trackedObservationBusy = new Set();
    const trackedObservationCardMessages = new Map();
    let trackedObservationListGeneration = 0;
    let pendingTrackedObservationFocus = "";

    function trackedObservationId(item) {
      return String(item?.tracking_id ?? item?.id ?? "").trim();
    }

    function trackedObservationSnapshot(item) {
      const snapshot = item?.original_snapshot;
      if (snapshot && typeof snapshot === "object" && !Array.isArray(snapshot)) return snapshot;
      if (typeof snapshot !== "string" || !snapshot.trim()) return {};
      try {
        const decoded = JSON.parse(snapshot);
        return decoded && typeof decoded === "object" && !Array.isArray(decoded) ? decoded : {};
      } catch {
        return {};
      }
    }

    function trackedObservationStatusMeta(item) {
      if (String(item?.status || "active") === "archived") {
        return { label: "已停止", tone: "archived" };
      }
      if ((Number(item?.unread_count) || 0) > 0) {
        return { label: "有新变化", tone: "changed" };
      }
      if (item?.last_check_status === "changed") return { label: "最近有变化", tone: "changed" };
      return {
        running: { label: "正在检查", tone: "progress" },
        unchanged: { label: "暂无变化", tone: "quiet" },
        valid_empty: { label: "没有新材料", tone: "quiet" },
        retry_wait: { label: "等待重试", tone: "warning" },
        failed: { label: "检查失败", tone: "danger" },
        cancelled: { label: "已停止", tone: "archived" },
        pending: { label: "等待首次检查", tone: "pending" },
      }[String(item?.last_check_status || "pending")] || { label: "等待检查", tone: "pending" };
    }

    function trackedObservationCheckMeta(check) {
      return {
        running: { label: "检查中", tone: "progress" },
        changed: { label: "发现变化", tone: "changed" },
        unchanged: { label: "没有变化", tone: "quiet" },
        valid_empty: { label: "没有新材料", tone: "quiet" },
        retry_wait: { label: "等待重试", tone: "warning" },
        failed: { label: "检查失败", tone: "danger" },
        cancelled: { label: "已取消", tone: "archived" },
      }[String(check?.status || "")] || { label: "检查记录", tone: "pending" };
    }

    function trackedObservationEvidence(check) {
      return asArray(check?.evidence ?? check?.evidences ?? check?.changes);
    }

    function trackedObservationChangeTypeLabel(value) {
      return {
        new_fact: "新增事实",
        confirmation: "再次确认",
        contradiction: "出现反证",
        progress: "事件推进",
        invalidated: "原判断失效",
      }[String(value || "")] || "变化证据";
    }

    function trackedObservationEvidenceHtml(evidence) {
      const snapshot = evidence?.evidence_snapshot && typeof evidence.evidence_snapshot === "object"
        ? evidence.evidence_snapshot
        : {};
      const title = String(snapshot.title || evidence?.summary || evidence?.fact || "新增证据").trim();
      const url = safeHttpContentUrl(snapshot.content_url || snapshot.original_url || "");
      const confidence = Number(evidence?.confidence);
      const confidenceLabel = Number.isFinite(confidence) && confidence > 0
        ? ` · 置信度 ${Math.round(confidence * 100)}%`
        : "";
      const fact = String(evidence?.fact || "").trim();
      const inference = String(evidence?.inference || "").trim();
      const previous = String(evidence?.previous_state || "").trim();
      const next = String(evidence?.new_state || "").trim();
      const quote = String(evidence?.evidence_quote || "").trim();
      return `
        <article class="tracking-evidence">
          <div class="tracking-evidence-head">
            <span>${escapeHtml(trackedObservationChangeTypeLabel(evidence?.change_type))}${escapeHtml(confidenceLabel)}</span>
            <time>${escapeHtml(contentHistoryTime(evidence?.detected_at))}</time>
          </div>
          <h5>${url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(title)}</a>` : escapeHtml(title)}</h5>
          ${previous || next ? `<div class="tracking-state-change"><p><span>之前</span>${escapeHtml(previous || "未记录")}</p><p><span>现在</span>${escapeHtml(next || "未记录")}</p></div>` : ""}
          ${fact ? `<p class="tracking-fact"><strong>事实</strong>${escapeHtml(fact)}</p>` : ""}
          ${inference ? `<p class="tracking-inference"><strong>推断</strong>${escapeHtml(inference)}</p>` : ""}
          ${quote ? `<blockquote>${escapeHtml(quote)}</blockquote>` : ""}
        </article>`;
    }

    function trackedObservationCheckHtml(check) {
      const meta = trackedObservationCheckMeta(check);
      const evidence = trackedObservationEvidence(check);
      const error = String(check?.error || "").trim();
      const summary = String(check?.summary || (error ? "本轮检查未完成。" : "这次检查没有补充说明。")).trim();
      const completedAt = check?.completed_at || check?.started_at;
      const countLine = `${Number(check?.candidate_count) || 0} 条候选 · ${Number(check?.matched_count) || 0} 条相关 · ${Number(check?.new_evidence_count) || evidence.length} 条变化`;
      return `
        <article class="tracking-check" data-tone="${escapeHtml(meta.tone)}">
          <header>
            <span class="tracking-status-chip" data-tone="${escapeHtml(meta.tone)}">${escapeHtml(meta.label)}</span>
            <time>${escapeHtml(contentHistoryTime(completedAt))}</time>
          </header>
          <p>${escapeHtml(summary)}</p>
          ${error && error !== summary ? `<p class="tracking-check-error"><strong>原因</strong>${escapeHtml(error)}</p>` : ""}
          <p class="tracking-check-counts">${escapeHtml(countLine)}</p>
          ${evidence.length ? `<div class="tracking-evidence-list">${evidence.map(trackedObservationEvidenceHtml).join("")}</div>` : ""}
        </article>`;
    }

    function trackedObservationTimelineHtml(trackingId) {
      const timeline = trackedObservationTimelineState.get(trackingId);
      if (!timeline || timeline.loading) {
        return '<p class="tracking-timeline-state">正在读取检查记录…</p>';
      }
      if (timeline.error) {
        return `<p class="tracking-timeline-state is-error" role="alert">${escapeHtml(timeline.error)} <button class="tracking-inline-action" data-tracking-action="timeline-retry" type="button">重试</button></p>`;
      }
      const checks = asArray(timeline.checks);
      if (!checks.length) {
        return '<p class="tracking-timeline-state">还没有检查记录。你可以点“立即检查”，用本机已经采集的新闻核对一次。</p>';
      }
      const moreState = timeline.moreError
        ? `<p class="tracking-timeline-state is-error" role="alert">${escapeHtml(timeline.moreError)} <button class="tracking-inline-action" data-tracking-action="timeline-more" type="button">重试加载</button></p>`
        : timeline.hasMore
          ? `<button class="tracking-load-more" data-tracking-action="timeline-more" type="button" ${timeline.loadingMore ? "disabled" : ""}>${timeline.loadingMore ? "正在加载…" : "加载更早记录"}</button>`
          : "";
      return `<div class="tracking-timeline-list">${checks.map(trackedObservationCheckHtml).join("")}</div>${moreState}`;
    }

    function trackedObservationCardHtml(item, index) {
      const trackingId = trackedObservationId(item);
      const snapshot = trackedObservationSnapshot(item);
      const status = trackedObservationStatusMeta(item);
      const active = String(item?.status || "active") === "active";
      const expanded = trackedObservationExpanded.has(trackingId);
      const unreadCount = Number(item?.unread_count) || 0;
      const busy = Array.from(trackedObservationBusy).some((entry) => entry.endsWith(`:${trackingId}`));
      const title = String(snapshot.title || item?.item_key || "原始新闻").trim();
      const sourceUrl = safeHttpContentUrl(snapshot.content_url || "");
      const topic = String(item?.topic_group || snapshot.topic_group || "").trim();
      const story = String(item?.story_key || snapshot.franchise_key || "").trim();
      const latestSummary = String(item?.last_check_summary || item?.latest_evidence || "").trim();
      const message = trackedObservationCardMessages.get(trackingId) || "";
      const lastCheckLabel = item?.last_checked_at
        ? `上次检查 ${contentHistoryTime(item.last_checked_at)}`
        : "尚未检查";
      const nextCheckLabel = active && item?.next_check_at
        ? `下次计划 ${contentHistoryTime(item.next_check_at)}`
        : "";
      return `
        <article class="tracking-card${unreadCount ? " has-unread" : ""}" data-tracking-id="${escapeHtml(trackingId)}" data-tracking-index="${index}" tabindex="-1">
          <div class="tracking-card-head">
            <div class="tracking-card-title-row">
              <span class="tracking-status-chip" data-tone="${escapeHtml(status.tone)}">${escapeHtml(status.label)}</span>
              ${unreadCount ? `<span class="tracking-unread-chip">${unreadCount} 条未读</span>` : ""}
            </div>
            <p class="tracking-card-times"><span>${escapeHtml(lastCheckLabel)}</span>${nextCheckLabel ? `<span>${escapeHtml(nextCheckLabel)}</span>` : ""}</p>
          </div>
          <div class="tracking-card-copy">
            <p class="tracking-card-kicker">观察点</p>
            <h3>${escapeHtml(item?.observation || "未命名观察点")}</h3>
            ${latestSummary ? `<p class="tracking-latest-summary">${escapeHtml(latestSummary)}</p>` : ""}
            <div class="tracking-context-chips">
              ${topic ? `<span>主题 · ${escapeHtml(topic)}</span>` : ""}
              ${story ? `<span>事件 · ${escapeHtml(story)}</span>` : ""}
            </div>
            <p class="tracking-origin">来自：${sourceUrl ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(title)}</a>` : escapeHtml(title)}</p>
          </div>
          <div class="tracking-card-actions" aria-label="持续跟踪操作">
            <button class="tracking-action is-primary" data-tracking-toggle type="button" aria-expanded="${expanded}" aria-controls="trackingTimeline-${escapeHtml(trackingId)}">${expanded ? "收起时间线" : "查看时间线"}</button>
            ${active ? `<button class="tracking-action" data-tracking-action="check" type="button" ${busy ? "disabled" : ""}>立即检查</button>` : ""}
            ${unreadCount ? `<button class="tracking-action" data-tracking-action="read" type="button" ${busy ? "disabled" : ""}>标为已读</button>` : ""}
            ${active ? `<button class="tracking-action is-danger" data-tracking-action="stop" type="button" ${busy ? "disabled" : ""}>停止跟踪</button>` : ""}
            ${active ? "" : `<button class="tracking-action" data-tracking-action="restart" type="button" ${busy ? "disabled" : ""}>重新开始</button>`}
          </div>
          <p class="tracking-card-message${message?.error ? " is-error" : ""}" aria-live="polite">${escapeHtml(message?.text || "")}</p>
          <section class="tracking-timeline" id="trackingTimeline-${escapeHtml(trackingId)}" aria-label="观察点检查时间线" ${expanded ? "" : "hidden"}>
            ${expanded ? trackedObservationTimelineHtml(trackingId) : ""}
          </section>
        </article>`;
    }

    function syncTrackedObservationRegistry(items) {
      for (const item of items) {
        const itemKey = String(item?.item_key || "").trim();
        const observation = String(item?.observation || "").trim();
        if (!itemKey || !observation) continue;
        const key = `${itemKey}\u0000${observation}`;
        if (String(item?.status || "active") !== "active") {
          trackedObservationRecords.delete(key);
          continue;
        }
        trackedObservationRecords.set(key, item);
        trackedObservationHydratedItems.add(itemKey);
      }
    }

    function updateTrackedObservationSummary() {
      const values = {
        trackingActiveCount: trackedObservationPageState.activeCount,
        trackingUnreadCount: trackedObservationPageState.unreadCount,
        trackingArchivedCount: trackedObservationPageState.archivedCount,
      };
      Object.entries(values).forEach(([id, value]) => {
        const target = document.getElementById(id);
        if (target) target.textContent = String(Number(value) || 0);
      });
      updateSavedBadge("trackedObservationsUnreadBadge", trackedObservationPageState.unreadCount);
    }

    function renderTrackedObservations() {
      const list = document.getElementById("trackingList");
      const empty = document.getElementById("trackingEmpty");
      const status = document.getElementById("trackingPageStatus");
      const refresh = document.getElementById("trackingRefreshBtn");
      updateTrackedObservationSummary();
      if (!list) return;
      list.setAttribute("aria-busy", trackedObservationPageState.loading ? "true" : "false");
      if (refresh) {
        refresh.disabled = trackedObservationPageState.loading;
        refresh.textContent = trackedObservationPageState.loading ? "刷新中…" : "刷新跟踪";
      }
      if (status) {
        status.classList.toggle("is-error", Boolean(trackedObservationPageState.error));
        status.setAttribute("role", trackedObservationPageState.error ? "alert" : "status");
        status.textContent = trackedObservationPageState.error
          ? trackedObservationPageState.error
          : trackedObservationPageState.loading
            ? "正在读取本机跟踪档案…"
            : trackedObservationPageState.total
              ? `共 ${trackedObservationPageState.total} 个观察点；检查只使用新闻爪已经采集到本机的新闻。`
              : "";
      }
      const items = trackedObservationPageState.items;
      if (!items.length) {
        list.replaceChildren();
        if (empty) empty.hidden = trackedObservationPageState.loading || Boolean(trackedObservationPageState.error);
        return;
      }
      if (empty) empty.hidden = true;
      const active = items.filter((item) => String(item?.status || "active") === "active");
      const archived = items.filter((item) => String(item?.status || "active") === "archived");
      list.innerHTML = [
        active.length ? `<section class="tracking-group" aria-labelledby="trackingActiveHeading"><div class="tracking-group-head"><h3 id="trackingActiveHeading">正在跟踪</h3><span>${active.length}</span></div>${active.map(trackedObservationCardHtml).join("")}</section>` : "",
        archived.length ? `<section class="tracking-group is-archived" aria-labelledby="trackingArchivedHeading"><div class="tracking-group-head"><h3 id="trackingArchivedHeading">已停止</h3><span>${archived.length}</span></div>${archived.map((item, index) => trackedObservationCardHtml(item, active.length + index)).join("")}</section>` : "",
        items.length < trackedObservationPageState.total ? `<button class="tracking-load-more" data-tracking-list-more type="button" ${trackedObservationPageState.loadingMore ? "disabled" : ""}>${trackedObservationPageState.loadingMore ? "正在加载…" : "加载更多观察点"}</button>` : "",
      ].join("");
      bindTrackedObservationCards();
      if (pendingTrackedObservationFocus) {
        const focusTarget = Array.from(list.querySelectorAll("[data-tracking-id]"))
          .find((element) => element.dataset.trackingId === pendingTrackedObservationFocus);
        if (focusTarget) {
          focusTarget.focus({ preventScroll: true });
          focusTarget.scrollIntoView({ block: "center", behavior: "smooth" });
          pendingTrackedObservationFocus = "";
        }
      }
    }

    function updateTrackedObservationItem(nextItem) {
      const trackingId = trackedObservationId(nextItem);
      if (!trackingId) return;
      const index = trackedObservationPageState.items.findIndex((item) => trackedObservationId(item) === trackingId);
      if (index >= 0) trackedObservationPageState.items[index] = { ...trackedObservationPageState.items[index], ...nextItem };
      else trackedObservationPageState.items.unshift(nextItem);
      syncTrackedObservationRegistry([nextItem]);
    }

    async function loadTrackedObservationTimeline(trackingId, { force = false, append = false } = {}) {
      if (!trackingId) return;
      const existing = trackedObservationTimelineState.get(trackingId);
      if (append && (existing?.loading || existing?.loadingMore || !existing?.hasMore)) return;
      if (!force && !append && (existing?.loading || Array.isArray(existing?.checks))) return;
      trackedObservationTimelineState.set(trackingId, {
        ...existing,
        loading: !append,
        loadingMore: append,
        error: "",
        moreError: "",
      });
      renderTrackedObservations();
      try {
        const offset = append ? asArray(existing?.checks).length : 0;
        const payload = await requestJsonStrict(
          `${ENDPOINTS.trackedObservations}/${encodeURIComponent(trackingId)}/timeline?limit=${TRACKED_OBSERVATION_TIMELINE_LIMIT}&offset=${offset}`,
          { timeoutMs: 20000 }
        );
        if (payload?.item) updateTrackedObservationItem(payload.item);
        const fetched = asArray(payload?.checks);
        const combined = append ? [...asArray(existing?.checks), ...fetched] : fetched;
        const uniqueChecks = Array.from(new Map(combined.map((check) => [String(check?.check_id || ""), check])).values());
        trackedObservationTimelineState.set(trackingId, {
          loading: false,
          loadingMore: false,
          error: "",
          moreError: "",
          item: payload?.item || null,
          checks: uniqueChecks,
          hasMore: fetched.length === TRACKED_OBSERVATION_TIMELINE_LIMIT,
        });
      } catch (error) {
        trackedObservationTimelineState.set(trackingId, {
          ...existing,
          loading: false,
          loadingMore: false,
          error: append ? "" : configErrorMessage(error?.details) || error?.message || "检查记录读取失败，请重试。",
          moreError: append ? configErrorMessage(error?.details) || error?.message || "更早记录读取失败，请重试。" : "",
        });
      }
      renderTrackedObservations();
    }

    async function loadMoreTrackedObservations() {
      if (trackedObservationPageState.loading || trackedObservationPageState.loadingMore) return;
      const generation = ++trackedObservationListGeneration;
      trackedObservationPageState.loadingMore = true;
      trackedObservationPageState.error = "";
      renderTrackedObservations();
      try {
        const payload = await requestJsonStrict(
          `${ENDPOINTS.trackedObservations}?status=all&limit=${TRACKED_OBSERVATION_LIMIT}&offset=${trackedObservationPageState.items.length}`,
          { timeoutMs: 20000 }
        );
        if (generation !== trackedObservationListGeneration) return;
        const combined = [...trackedObservationPageState.items, ...trackedObservationList(payload)];
        trackedObservationPageState.items = Array.from(new Map(combined.map((item) => [trackedObservationId(item), item])).values());
        trackedObservationPageState.total = payload?.total == null ? trackedObservationPageState.items.length : Math.max(0, Number(payload.total) || 0);
        syncTrackedObservationRegistry(trackedObservationPageState.items);
      } catch (error) {
        if (generation !== trackedObservationListGeneration) return;
        trackedObservationPageState.error = configErrorMessage(error?.details) || error?.message || "更多观察点读取失败，请重试。";
      } finally {
        if (generation === trackedObservationListGeneration) {
          trackedObservationPageState.loadingMore = false;
          renderTrackedObservations();
        }
      }
    }

    function mergeTrackedObservationRefresh(existingItems, refreshedItems, total, preserveLoadedTail) {
      const refreshed = asArray(refreshedItems);
      const cappedTotal = Math.max(0, Number(total) || 0);
      if (!preserveLoadedTail || asArray(existingItems).length <= refreshed.length) {
        return refreshed.slice(0, cappedTotal);
      }
      const refreshedIds = new Set(refreshed.map(trackedObservationId));
      return [
        ...refreshed,
        ...asArray(existingItems).filter((item) => !refreshedIds.has(trackedObservationId(item))),
      ].slice(0, cappedTotal);
    }

    async function refreshTrackedObservations({ silent = false, focusTrackingId = "" } = {}) {
      const generation = ++trackedObservationListGeneration;
      if (focusTrackingId) {
        pendingTrackedObservationFocus = String(focusTrackingId);
        trackedObservationExpanded.add(String(focusTrackingId));
      }
      trackedObservationPageState.loading = !silent;
      trackedObservationPageState.loadingMore = false;
      trackedObservationPageState.error = "";
      if (!silent) renderTrackedObservations();
      try {
        const payload = await requestJsonStrict(
          `${ENDPOINTS.trackedObservations}?status=all&limit=${Math.min(500, Math.max(TRACKED_OBSERVATION_LIMIT, trackedObservationPageState.items.length))}&offset=0`,
          { timeoutMs: 20000 }
        );
        if (generation !== trackedObservationListGeneration) return;
        const refreshedItems = trackedObservationList(payload);
        const total = payload?.total == null ? refreshedItems.length : Math.max(0, Number(payload.total) || 0);
        const items = mergeTrackedObservationRefresh(
          trackedObservationPageState.items,
          refreshedItems,
          total,
          silent,
        );
        const activeCount = items.filter((item) => String(item?.status || "active") === "active").length;
        const archivedCount = items.filter((item) => String(item?.status || "active") === "archived").length;
        const unreadCount = items.reduce((sum, item) => sum + (Number(item?.unread_count) || 0), 0);
        trackedObservationPageState.items = items;
        trackedObservationPageState.total = total;
        trackedObservationPageState.activeCount = payload?.active_count == null ? activeCount : Math.max(0, Number(payload.active_count) || 0);
        trackedObservationPageState.archivedCount = payload?.archived_count == null ? archivedCount : Math.max(0, Number(payload.archived_count) || 0);
        trackedObservationPageState.unreadCount = payload?.unread_count == null ? unreadCount : Math.max(0, Number(payload.unread_count) || 0);
        trackedObservationPageState.error = "";
        syncTrackedObservationRegistry(items);
      } catch (error) {
        if (generation !== trackedObservationListGeneration) return;
        trackedObservationPageState.error = configErrorMessage(error?.details) || error?.message || "持续跟踪读取失败，请重试。";
      } finally {
        if (generation === trackedObservationListGeneration) {
          trackedObservationPageState.loading = false;
          renderTrackedObservations();
          if (focusTrackingId) void loadTrackedObservationTimeline(String(focusTrackingId));
        }
      }
    }

    async function runTrackedObservationAction(action, trackingId) {
      const busyKey = `${action}:${trackingId}`;
      if (!trackingId || trackedObservationBusy.has(busyKey)) return;
      trackedObservationBusy.add(busyKey);
      trackedObservationCardMessages.set(trackingId, {
        text: action === "check" ? "正在用本机已采集的新闻检查变化…" : action === "read" ? "正在标记已读…" : action === "restart" ? "正在建立新的跟踪周期…" : "正在停止跟踪…",
        error: false,
      });
      renderTrackedObservations();
      try {
        let payload;
        let focusAfterAction = trackingId;
        if (action === "check") {
          payload = await requestJsonStrict(`${ENDPOINTS.trackedObservations}/${encodeURIComponent(trackingId)}/check`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ request_id: newRequestId() }),
            timeoutMs: 300000,
          });
          if (payload?.item) updateTrackedObservationItem(payload.item);
          if (payload?.check) {
            const current = trackedObservationTimelineState.get(trackingId);
            const checks = asArray(current?.checks).filter((check) => check?.check_id !== payload.check.check_id);
            trackedObservationTimelineState.set(trackingId, { ...current, loading: false, loadingMore: false, error: "", moreError: "", item: payload.item, checks: [payload.check, ...checks] });
          }
          trackedObservationExpanded.add(trackingId);
          const checkMeta = trackedObservationCheckMeta(payload?.check);
          trackedObservationCardMessages.set(trackingId, { text: `检查完成：${checkMeta.label}。`, error: false });
          showToast(`持续跟踪：${checkMeta.label}`);
        } else if (action === "read") {
          payload = await requestJsonStrict(`${ENDPOINTS.trackedObservations}/${encodeURIComponent(trackingId)}/read`, {
            method: "POST",
            timeoutMs: 20000,
          });
          if (payload?.item) updateTrackedObservationItem(payload.item);
          const timeline = trackedObservationTimelineState.get(trackingId);
          if (timeline?.checks) {
            trackedObservationTimelineState.set(trackingId, {
              ...timeline,
              checks: timeline.checks.map((check) => ({ ...check, is_read: true })),
            });
          }
          trackedObservationCardMessages.set(trackingId, { text: "变化记录已标为已读。", error: false });
          showToast("未读变化已标为已读");
        } else if (action === "restart") {
          payload = await requestJsonStrict(`${ENDPOINTS.trackedObservations}/${encodeURIComponent(trackingId)}/restart`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ request_id: newRequestId() }),
            timeoutMs: 20000,
          });
          const restartedId = trackedObservationId(payload?.item);
          if (payload?.item) updateTrackedObservationItem(payload.item);
          if (restartedId) {
            trackedObservationExpanded.add(restartedId);
            pendingTrackedObservationFocus = restartedId;
            focusAfterAction = restartedId;
          }
          trackedObservationCardMessages.set(trackingId, { text: "已建立新的跟踪周期；旧时间线仍保留。", error: false });
          showToast(payload?.created ? "已重新开始持续跟踪" : "这个观察点已经在跟踪中");
        } else if (action === "stop") {
          payload = await requestJsonStrict(`${ENDPOINTS.trackedObservations}/${encodeURIComponent(trackingId)}`, {
            method: "DELETE",
            timeoutMs: 20000,
          });
          if (payload?.item) updateTrackedObservationItem(payload.item);
          const archived = payload?.item;
          const itemKey = String(archived?.item_key || "").trim();
          const observation = String(archived?.observation || "").trim();
          if (itemKey && observation) trackedObservationRecords.delete(`${itemKey}\u0000${observation}`);
          trackedObservationCardMessages.set(trackingId, { text: "已停止跟踪；已有检查记录仍保留。", error: false });
          showToast("已停止持续跟踪");
        }
        await refreshTrackedObservations({ silent: true, focusTrackingId: focusAfterAction });
      } catch (error) {
        trackedObservationCardMessages.set(trackingId, {
          text: configErrorMessage(error?.details) || error?.message || "操作失败，请重试。",
          error: true,
        });
      } finally {
        trackedObservationBusy.delete(busyKey);
        renderTrackedObservations();
      }
    }

    function bindTrackedObservationCards() {
      const list = document.getElementById("trackingList");
      list?.querySelectorAll("[data-tracking-toggle]").forEach((button) => {
        button.addEventListener("click", () => {
          const card = button.closest("[data-tracking-id]");
          const trackingId = String(card?.dataset.trackingId || "");
          if (!trackingId) return;
          if (trackedObservationExpanded.has(trackingId)) trackedObservationExpanded.delete(trackingId);
          else trackedObservationExpanded.add(trackingId);
          renderTrackedObservations();
          if (trackedObservationExpanded.has(trackingId)) void loadTrackedObservationTimeline(trackingId);
        });
      });
      list?.querySelectorAll("[data-tracking-action]").forEach((button) => {
        button.addEventListener("click", () => {
          const card = button.closest("[data-tracking-id]");
          const trackingId = String(card?.dataset.trackingId || "");
          const action = String(button.dataset.trackingAction || "");
          if (action === "timeline-retry") void loadTrackedObservationTimeline(trackingId, { force: true });
          else if (action === "timeline-more") void loadTrackedObservationTimeline(trackingId, { append: true });
          else void runTrackedObservationAction(action, trackingId);
        });
      });
      list?.querySelector("[data-tracking-list-more]")?.addEventListener("click", () => {
        void loadMoreTrackedObservations();
      });
    }

    function openTrackedObservationInLibrary(record) {
      const trackingId = trackedObservationId(record);
      if (trackingId) {
        trackedObservationExpanded.add(trackingId);
        pendingTrackedObservationFocus = trackingId;
      }
      openContentLibraryPage("tracking", { forceLoad: true });
      if (trackingId) void loadTrackedObservationTimeline(trackingId);
    }

    function webMonitorRequestId(prefix = "monitor") {
      const id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
      return `${prefix}:${id}`;
    }

    function webMonitorStatusLabel(item) {
      return {
        active: item?.has_baseline ? "运行正常" : "等待基线",
        paused: "已暂停",
        retry_wait: "稍后重试",
        needs_attention: "需要处理",
      }[String(item?.status || "")] || "状态未知";
    }

    function webMonitorTime(value, fallback = "—") {
      if (!value) return fallback;
      const formatted = formatLocalTime(String(value));
      return formatted || fallback;
    }

    function webMonitorHistoryHtml(item) {
      const monitorId = String(item?.monitor_id || "");
      const record = state.webMonitors.histories.get(monitorId);
      if (!record) return "";
      if (record.loading) return '<div class="monitor-history is-loading">正在读取版本记录…</div>';
      if (record.error) return `<div class="monitor-history is-error">${escapeHtml(record.error)}</div>`;
      const checks = asArray(record.data?.checks).slice(0, 6);
      const diffs = asArray(record.data?.diffs).slice(0, 4);
      return `<div class="monitor-history">
        <div class="monitor-history-head"><strong>最近版本</strong><span>${asArray(record.data?.versions).length} 个本地版本</span></div>
        ${diffs.length ? `<div class="monitor-diff-list">${diffs.map((diff) => `<div><span class="monitor-history-dot is-change"></span><strong>${escapeHtml(diff.summary || "正文变化")}</strong><time>${escapeHtml(webMonitorTime(diff.created_at))}</time></div>`).join("")}</div>` : '<p>还没有正文变化；首次成功检查只会建立基线。</p>'}
        <div class="monitor-check-strip">${checks.map((check) => `<span class="monitor-check-pill is-${escapeHtml(String(check.status || "unknown"))}" title="${escapeHtml(check.error_message || "")}">${escapeHtml({ baseline: "基线", unchanged: "无变化", noise_only: "已降噪", changed: "有变化", retry_wait: "待重试", failed: "失败", cancelled: "已取消", running: "检查中" }[check.status] || check.status || "未知")}</span>`).join("")}</div>
      </div>`;
    }

    function renderWebMonitors() {
      const webState = state.webMonitors;
      const list = document.getElementById("monitoringList");
      const empty = document.getElementById("monitoringEmpty");
      const statusLine = document.getElementById("monitoringStatus");
      if (!list) return;
      const items = Array.isArray(webState.items) ? webState.items : null;
      const runtime = webState.runtime || {};
      const activeCount = document.getElementById("monitoringActiveCount");
      const unreadCount = document.getElementById("monitoringUnreadCount");
      const failedCount = document.getElementById("monitoringFailedCount");
      const unread = items ? items.reduce((total, item) => total + Number(item.unread_count || 0), 0) : null;
      if (activeCount) activeCount.textContent = items ? String(items.filter((item) => ["active", "retry_wait"].includes(item.status)).length) : "—";
      if (unreadCount) unreadCount.textContent = unread === null ? "—" : String(unread);
      if (failedCount) failedCount.textContent = items ? String(items.filter((item) => item.status === "needs_attention").length) : "—";
      const badge = document.getElementById("webMonitorUnreadBadge");
      if (badge) {
        badge.textContent = unread === null ? "" : String(unread);
        badge.hidden = !unread;
      }
      list.setAttribute("aria-busy", String(Boolean(webState.loading)));
      if (webState.loading && items === null) {
        list.innerHTML = '<div class="monitoring-loading"><span></span><span></span><span></span></div>';
        if (empty) empty.hidden = true;
        if (statusLine) statusLine.textContent = "正在读取这台电脑上的监控记录…";
        return;
      }
      if (webState.error && items === null) {
        list.innerHTML = `<div class="monitoring-error"><strong>暂时没读取到监控记录</strong><p>${escapeHtml(webState.error)}</p><button class="pill-btn" data-monitor-retry type="button">重新读取</button></div>`;
        list.querySelector("[data-monitor-retry]")?.addEventListener("click", () => void refreshWebMonitors());
        if (empty) empty.hidden = true;
        if (statusLine) statusLine.textContent = "";
        return;
      }
      if (statusLine) {
        statusLine.textContent = webState.error
          ? `刷新失败，仍显示上次记录：${webState.error}`
          : runtime.running
            ? `正在检查 ${runtime.running} 项`
            : "";
      }
      const rows = items || [];
      if (empty) empty.hidden = rows.length > 0;
      list.replaceChildren(...rows.map((item) => {
        const card = document.createElement("article");
        const monitorId = String(item.monitor_id || "");
        const busy = webState.busyIds.has(monitorId) || Boolean(item.is_running);
        const label = item.label || item.snapshot_title || (() => {
          try { return new URL(item.display_url || item.canonical_url).hostname; } catch { return "网页监控"; }
        })();
        const status = String(item.status || "");
        const statusClass = status === "needs_attention" ? "is-error" : status === "retry_wait" ? "is-waiting" : status === "paused" ? "is-paused" : "is-healthy";
        card.className = `monitor-card ${statusClass}`;
        card.dataset.monitorId = monitorId;
        card.innerHTML = `
          <div class="monitor-card-main">
            <div class="monitor-card-title-row"><span class="monitor-status-dot" aria-hidden="true"></span><h3>${escapeHtml(label)}</h3><span class="monitor-status-label">${escapeHtml(busy ? "正在检查" : webMonitorStatusLabel(item))}</span></div>
            <a class="monitor-url" href="${escapeHtml(item.canonical_url || item.display_url || "#")}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.display_url || item.canonical_url || "")}</a>
            <div class="monitor-facts">
              <span>版本 <strong>${item.current_version || "—"}</strong></span>
              <span>变化 <strong>${Number(item.change_count || 0)}</strong></span>
              <span>上次成功 <strong>${escapeHtml(webMonitorTime(item.last_success_at))}</strong></span>
              <span>下次 <strong>${escapeHtml(status === "paused" ? "暂停中" : webMonitorTime(item.next_check_at))}</strong></span>
            </div>
            ${item.last_error ? `<p class="monitor-card-error">${escapeHtml(item.last_error)}</p>` : ""}
          </div>
          <div class="monitor-card-actions">
            <button class="small-btn" data-monitor-action="event" type="button">事件档案</button>
            <button class="small-btn" data-monitor-action="history" type="button">版本</button>
            <button class="small-btn" data-monitor-action="check" type="button" ${busy || status === "paused" ? "disabled" : ""}>立即检查</button>
            <button class="small-btn" data-monitor-action="${status === "paused" ? "resume" : "pause"}" type="button">${status === "paused" ? "恢复" : "暂停"}</button>
            <button class="small-btn is-danger" data-monitor-action="delete" type="button">移除</button>
          </div>
          ${webMonitorHistoryHtml(item)}`;
        return card;
      }));
    }

    async function refreshWebMonitors({ silent = false } = {}) {
      if (state.newsclawMode !== true) return;
      const generation = ++state.webMonitors.refreshGeneration;
      state.webMonitors.refreshRequested = false;
      state.webMonitors.loading = true;
      state.webMonitors.error = "";
      if (!silent) renderWebMonitors();
      const request = (async () => {
        try {
          const payload = await requestJsonStrict(ENDPOINTS.webMonitors, { timeoutMs: 15000, cache: "no-store" });
          if (
            state.newsclawMode !== true
            || generation !== state.webMonitors.refreshGeneration
          ) return;
          state.webMonitors.items = asArray(payload?.items);
          state.webMonitors.runtime = payload?.runtime || {};
        } catch (error) {
          if (
            state.newsclawMode === true
            && generation === state.webMonitors.refreshGeneration
          ) {
            state.webMonitors.error = configErrorMessage(error?.details) || error?.message || "本地后端暂时不可用";
          }
        }
      })();
      state.webMonitors.refreshPromise = request;
      try {
        await request;
      } finally {
        if (generation === state.webMonitors.refreshGeneration) {
          state.webMonitors.loading = false;
          if (state.webMonitors.refreshPromise === request) {
            state.webMonitors.refreshPromise = null;
          }
          renderWebMonitors();
        }
      }
    }

    async function createWebMonitor() {
      if (state.newsclawMode !== true) return;
      const urlInput = document.getElementById("monitoringUrlInput");
      const labelInput = document.getElementById("monitoringLabelInput");
      const intervalInput = document.getElementById("monitoringIntervalSelect");
      const submit = document.getElementById("monitoringAddBtn");
      const url = String(urlInput?.value || "").trim();
      if (!url) return;
      if (submit) submit.disabled = true;
      if (document.getElementById("monitoringStatus")) document.getElementById("monitoringStatus").textContent = "正在安全读取并建立首个正文基线…";
      try {
        const payload = await requestJsonStrict(ENDPOINTS.webMonitors, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            url,
            label: String(labelInput?.value || "").trim(),
            kind: "auto",
            interval_minutes: Number(intervalInput?.value || 1440),
            request_id: webMonitorRequestId("create"),
          }),
          timeoutMs: 120000,
        });
        if (urlInput) urlInput.value = "";
        if (labelInput) labelInput.value = "";
        state.webMonitors.items = null;
        showToast(payload?.check?.status === "baseline" ? "已建立基线，以后只提醒真实变化" : "网页监控已保存");
        await refreshWebMonitors({ silent: true });
      } catch (error) {
        const message = configErrorMessage(error?.details) || error?.message || "添加失败";
        state.webMonitors.error = message;
        showToast(message);
        renderWebMonitors();
      } finally {
        if (submit) submit.disabled = false;
      }
    }

    async function runWebMonitorAction(action, monitorId) {
      if (!action || !monitorId || state.webMonitors.busyIds.has(monitorId)) return;
      const item = asArray(state.webMonitors.items).find((row) => String(row.monitor_id) === monitorId);
      if (!item) return;
      if (action === "event") {
        openHomePage("events");
        await eventWorkspaceController?.openEvent?.(String(item.event_id || ""));
        return;
      }
      if (action === "history") {
        const generation = (state.webMonitors.historyGenerations.get(monitorId) || 0) + 1;
        state.webMonitors.historyGenerations.set(monitorId, generation);
        if (state.webMonitors.histories.has(monitorId)) {
          state.webMonitors.histories.delete(monitorId);
          renderWebMonitors();
          return;
        }
        state.webMonitors.histories.set(monitorId, { loading: true, data: null, error: "" });
        renderWebMonitors();
        try {
          const data = await requestJsonStrict(`${ENDPOINTS.webMonitors}/${encodeURIComponent(monitorId)}/history`, { timeoutMs: 15000, cache: "no-store" });
          if (state.webMonitors.historyGenerations.get(monitorId) !== generation) return;
          state.webMonitors.histories.set(monitorId, { loading: false, data, error: "" });
        } catch (error) {
          if (state.webMonitors.historyGenerations.get(monitorId) !== generation) return;
          state.webMonitors.histories.set(monitorId, { loading: false, data: null, error: configErrorMessage(error?.details) || error?.message || "读取失败" });
        }
        renderWebMonitors();
        return;
      }
      if (action === "delete" && !window.confirm("移除这个监控？已有版本和事件档案会保留在本机。")) return;
      const historyWasOpen = action === "check" && state.webMonitors.histories.has(monitorId);
      let historyGeneration = 0;
      state.webMonitors.busyIds.add(monitorId);
      renderWebMonitors();
      try {
        if (action === "check") {
          const result = await requestJsonStrict(`${ENDPOINTS.webMonitors}/${encodeURIComponent(monitorId)}/check`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ request_id: webMonitorRequestId("check") }),
            timeoutMs: 120000,
          });
          const checkStatus = String(result?.status || result?.check?.status || "");
          if (historyWasOpen && checkStatus) {
            historyGeneration = (state.webMonitors.historyGenerations.get(monitorId) || 0) + 1;
            state.webMonitors.historyGenerations.set(monitorId, historyGeneration);
            state.webMonitors.histories.set(monitorId, { loading: true, data: null, error: "" });
          }
          if (checkStatus === "changed") showToast("发现正文变化，已写入事件档案和消息");
          else if (checkStatus === "baseline") showToast("已建立正文基线");
          else if (["unchanged", "noise_only"].includes(checkStatus)) showToast("检查完成，正文没有确认变化");
          else if (checkStatus === "retry_wait") showToast(result?.error || "本次读取失败，系统会自动重试");
          else if (checkStatus === "failed") showToast(result?.error || "本次检查失败，需要处理");
          else if (checkStatus === "cancelled") showToast("本次检查已取消");
          else showToast("检查结果状态未知，请刷新后查看");
        } else if (action === "delete") {
          await requestJsonStrict(`${ENDPOINTS.webMonitors}/${encodeURIComponent(monitorId)}`, { method: "DELETE", timeoutMs: 15000 });
          showToast("已移除监控，历史档案仍保留在本机");
        } else if (["pause", "resume"].includes(action)) {
          await requestJsonStrict(`${ENDPOINTS.webMonitors}/${encodeURIComponent(monitorId)}/${action}`, { method: "POST", timeoutMs: 15000 });
          showToast(action === "pause" ? "已暂停" : "已恢复监控");
        }
        await Promise.all([refreshWebMonitors({ silent: true }), refreshWebMonitorNotifications()]);
        if (historyWasOpen && historyGeneration) {
          try {
            const history = await requestJsonStrict(`${ENDPOINTS.webMonitors}/${encodeURIComponent(monitorId)}/history`, { timeoutMs: 15000, cache: "no-store" });
            if (state.webMonitors.historyGenerations.get(monitorId) !== historyGeneration) return;
            state.webMonitors.histories.set(monitorId, { loading: false, data: history, error: "" });
          } catch (historyError) {
            if (state.webMonitors.historyGenerations.get(monitorId) !== historyGeneration) return;
            const historyMessage = configErrorMessage(historyError?.details) || historyError?.message || "版本列表刷新失败";
            state.webMonitors.histories.set(monitorId, { loading: false, data: null, error: historyMessage });
            showToast(`检查已完成，但${historyMessage}`);
          }
        }
      } catch (error) {
        showToast(configErrorMessage(error?.details) || error?.message || "操作失败");
      } finally {
        state.webMonitors.busyIds.delete(monitorId);
        renderWebMonitors();
      }
    }

    const DESKTOP_CONTENT_LIBRARY_STORAGE_KEY = "openbiliclaw.webui.contentLibraryTab";
    const DESKTOP_CONTENT_LIBRARY_TABS = ["watchLater", "favorites", "tracking", "monitoring", "history"];
    const desktopContentLibraryScroll = new Map();
    let desktopContentLibraryTab = "watchLater";
    let desktopContentLibraryVisible = false;

    function normalizeDesktopContentLibraryTab(value, fallback = "watchLater") {
      const normalized = String(value || "").trim().toLowerCase();
      const mapped = {
        watchlater: "watchLater",
        "watch-later": "watchLater",
        watch_later: "watchLater",
        favorites: "favorites",
        favorite: "favorites",
        tracking: "tracking",
        tracked: "tracking",
        following: "tracking",
        monitoring: "monitoring",
        monitor: "monitoring",
        monitors: "monitoring",
        history: "history"
      }[normalized] || fallback;
      if (mapped !== "monitoring" || state.newsclawMode !== false) return mapped;
      return fallback === "monitoring" ? "watchLater" : fallback;
    }

    function storedDesktopContentLibraryTab() {
      return normalizeDesktopContentLibraryTab(storageGet(DESKTOP_CONTENT_LIBRARY_STORAGE_KEY));
    }

    function desktopContentLibrarySlug(tab) {
      return tab === "watchLater" ? "watch-later" : tab;
    }

    function desktopContentLibraryHashTab() {
      const hash = window.location.hash.replace(/^#\/?/, "");
      const [parent, child] = hash.split("/");
      if (parent === "library") return normalizeDesktopContentLibraryTab(child, storedDesktopContentLibraryTab());
      if (["watchLater", "watch-later", "watch_later", "favorites", "favorite", "tracking", "tracked", "following", "monitoring", "monitor", "monitors", "history"].includes(parent)) {
        return normalizeDesktopContentLibraryTab(parent);
      }
      return "";
    }

    function clearDesktopContentLibraryRoute() {
      if (!desktopContentLibraryHashTab()) return;
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
    }

    function leaveDesktopContentLibrary() {
      if (!desktopContentLibraryVisible) return;
      desktopContentLibraryScroll.set(desktopContentLibraryTab, window.scrollY);
      desktopContentLibraryVisible = false;
    }

    function loadDesktopContentLibraryTab(tab) {
      if (tab === "watchLater") void refreshWatchLater();
      else if (tab === "favorites") void refreshFavorites();
      else if (tab === "tracking") void refreshTrackedObservations();
      else if (tab === "monitoring") void refreshWebMonitors();
      else void refreshContentHistory();
    }

    function activateDesktopContentLibraryTab(value, { focus = false, entering = false, forceLoad = false } = {}) {
      const tab = normalizeDesktopContentLibraryTab(value, desktopContentLibraryTab);
      const changed = desktopContentLibraryTab !== tab;
      if (desktopContentLibraryVisible && changed) {
        desktopContentLibraryScroll.set(desktopContentLibraryTab, window.scrollY);
      }
      desktopContentLibraryTab = tab;
      storageSet(DESKTOP_CONTENT_LIBRARY_STORAGE_KEY, tab);
      DESKTOP_CONTENT_LIBRARY_TABS.forEach((name) => {
        const suffix = { watchLater: "WatchLater", favorites: "Favorites", tracking: "Tracking", monitoring: "Monitoring", history: "History" }[name];
        const button = document.getElementById(`contentLibrary${suffix}Tab`);
        const panel = document.getElementById({ watchLater: "watchLaterPage", favorites: "favoritesPage", tracking: "trackingPage", monitoring: "monitoringPage", history: "historyPage" }[name]);
        const selected = name === tab;
        button?.classList.toggle("is-active", selected);
        button?.setAttribute("aria-selected", String(selected));
        if (button) button.tabIndex = selected ? 0 : -1;
        if (panel) panel.hidden = !selected;
      });
      if (changed || entering || forceLoad) loadDesktopContentLibraryTab(tab);
      if (changed || entering) window.requestAnimationFrame(() => {
        window.scrollTo({ top: desktopContentLibraryScroll.get(tab) || 0, behavior: "auto" });
        if (focus) document.querySelector('.content-library-tab[aria-selected="true"]')?.focus();
      });
      else if (focus) document.querySelector('.content-library-tab[aria-selected="true"]')?.focus();
    }

    function openContentLibraryPage(tab = storedDesktopContentLibraryTab(), { updateHash = true, focus = false, forceLoad = false } = {}) {
      const entering = !desktopContentLibraryVisible;
      leavePersonalMemory();
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("contentLibraryPage");
      desktopContentLibraryVisible = true;
      activateDesktopContentLibraryTab(tab, { focus, entering, forceLoad });
      if (updateHash) {
        const nextHash = `#library/${desktopContentLibrarySlug(desktopContentLibraryTab)}`;
        if (window.location.hash !== nextHash) window.location.hash = nextHash;
      }
    }

    // Retained entry points keep older internal links working while routing
    // them through the compact content-library shell.
    function openWatchLaterPage() {
      openContentLibraryPage("watchLater");
    }

    function openFavoritesPage() {
      openContentLibraryPage("favorites");
    }

    function openHistoryPage() {
      openContentLibraryPage("history");
    }

    function bindDesktopContentLibrary() {
      const buttons = DESKTOP_CONTENT_LIBRARY_TABS.map((name) => {
        const suffix = { watchLater: "WatchLater", favorites: "Favorites", tracking: "Tracking", monitoring: "Monitoring", history: "History" }[name];
        return document.getElementById(`contentLibrary${suffix}Tab`);
      }).filter((button) => button instanceof HTMLButtonElement);
      buttons.forEach((button, index) => {
        button.addEventListener("click", () => openContentLibraryPage(
          DESKTOP_CONTENT_LIBRARY_TABS[index],
          { forceLoad: true }
        ));
        button.addEventListener("keydown", (event) => {
          const currentTab = DESKTOP_CONTENT_LIBRARY_TABS[index];
          const availableTabs = DESKTOP_CONTENT_LIBRARY_TABS.filter(
            (name) => name !== "monitoring" || state.newsclawMode === true
          );
          const currentIndex = Math.max(0, availableTabs.indexOf(currentTab));
          let nextIndex = null;
          if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % availableTabs.length;
          else if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + availableTabs.length) % availableTabs.length;
          else if (event.key === "Home") nextIndex = 0;
          else if (event.key === "End") nextIndex = availableTabs.length - 1;
          if (nextIndex === null) return;
          event.preventDefault();
          openContentLibraryPage(availableTabs[nextIndex], { focus: true });
        });
      });
      document.getElementById("trackingRefreshBtn")?.addEventListener("click", () => {
        void refreshTrackedObservations();
      });
      document.getElementById("monitoringRefreshBtn")?.addEventListener("click", () => {
        void refreshWebMonitors();
      });
      document.getElementById("monitoringComposer")?.addEventListener("submit", (event) => {
        event.preventDefault();
        void createWebMonitor();
      });
      document.getElementById("monitoringList")?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-monitor-action]");
        if (!button) return;
        void runWebMonitorAction(
          String(button.dataset.monitorAction || ""),
          String(button.closest("[data-monitor-id]")?.dataset.monitorId || "")
        );
      });
      window.addEventListener("hashchange", () => {
        const tab = desktopContentLibraryHashTab();
        if (tab) openContentLibraryPage(tab, { updateHash: false });
        else if (desktopContentLibraryVisible) {
          leaveDesktopContentLibrary();
          showMainPage("homePage");
          setHomeMode(state.homeMode, { persist: false });
          window.scrollTo({ top: 0, behavior: "auto" });
        }
      });
      const initialTab = desktopContentLibraryHashTab();
      if (initialTab) {
        const canonicalHash = `#library/${desktopContentLibrarySlug(initialTab)}`;
        if (window.location.hash !== canonicalHash) {
          window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${canonicalHash}`);
        }
        openContentLibraryPage(initialTab, { updateHash: false });
      }
    }

    // ── P4 personal memory ───────────────────────────────────────
    // This UI only displays user-confirmed memory and pending inference
    // candidates. Legacy Soul/Profile inference stays mounted in a hidden
    // compatibility node and must never overwrite this page.
    const PERSONAL_MEMORY_TABS = ["confirmed", "research", "candidates", "history"];
    const PERSONAL_MEMORY_TAB_IDS = {
      confirmed: "memoryConfirmedTab",
      research: "memoryResearchTab",
      candidates: "memoryCandidatesTab",
      history: "memoryHistoryTab",
    };
    const PERSONAL_MEMORY_PANEL_IDS = {
      confirmed: "memoryConfirmedPanel",
      research: "memoryResearchPanel",
      candidates: "memoryCandidatesPanel",
      history: "memoryHistoryPanel",
    };
    const PERSONAL_MEMORY_LIST_IDS = {
      confirmed: "memoryConfirmedList",
      research: "memoryResearchList",
      candidates: "memoryCandidatesList",
      history: "memoryHistoryList",
    };
    let personalMemoryVisible = false;
    let personalMemoryRefreshPromise = null;

    function normalizePersonalMemoryTab(value, fallback = "confirmed") {
      const normalized = String(value || "").trim().toLowerCase();
      return PERSONAL_MEMORY_TABS.includes(normalized) ? normalized : fallback;
    }

    function personalMemoryHashTab() {
      const hash = window.location.hash.replace(/^#\/?/, "");
      const [parent, child] = hash.split("/");
      if (parent !== "memory") return "";
      return normalizePersonalMemoryTab(child);
    }

    function clearPersonalMemoryRoute() {
      if (!personalMemoryHashTab()) return;
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
    }

    function leavePersonalMemory() {
      personalMemoryVisible = false;
    }

    function setPersonalMemoryStatus(message = "", tone = "neutral") {
      const element = document.getElementById("memoryPageStatus");
      if (!element) return;
      element.textContent = String(message || "");
      element.dataset.tone = tone;
      element.setAttribute("role", tone === "error" ? "alert" : "status");
    }

    function personalMemoryErrorCode(error) {
      const details = error?.details;
      return String(details?.detail?.code || details?.code || "");
    }

    function personalMemorySourceLabel(sourceType) {
      return {
        user: "由你记录",
        confirmed_inference: "由你确认的系统建议",
        system_inference: "系统推断，待你确认",
        chat: "来自对话，待你确认",
        event: "来自事件，待你确认",
        import: "来自导入，待你确认",
      }[String(sourceType || "")] || "本机记录";
    }

    function personalMemoryKindLabel(kind) {
      return kind === "research_direction" ? "研究方向" : "长期信息";
    }

    function personalMemoryDate(value) {
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return String(value);
      return new Intl.DateTimeFormat("zh-CN", {
        year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
      }).format(date);
    }

    function personalMemoryEvidenceHtml(evidence, { sourceRef = "" } = {}) {
      const rows = asArray(evidence).filter((item) => item && typeof item === "object");
      const cleanSourceRef = String(sourceRef || "").trim();
      if (cleanSourceRef && !rows.some((item) => String(item.ref || "") === cleanSourceRef)) {
        rows.unshift({ type: "来源记录", ref: cleanSourceRef, excerpt: "" });
      }
      if (!rows.length) return "";
      return `<details class="memory-evidence"><summary>查看依据（${rows.length}）</summary><div class="memory-evidence-list">${rows.map((item) => {
        const ref = String(item.ref || "");
        const refHtml = /^https?:\/\//i.test(ref)
          ? `<a href="${escapeHtml(ref)}" target="_blank" rel="noopener noreferrer">${escapeHtml(ref)}</a>`
          : escapeHtml(ref || "本机记录");
        return `<div class="memory-evidence-item"><strong>${escapeHtml(item.type || "记录")}</strong><span>${refHtml}</span>${item.excerpt ? `<p>${escapeHtml(item.excerpt)}</p>` : ""}</div>`;
      }).join("")}</div></details>`;
    }

    function personalMemoryEditForm(item) {
      const draft = state.personalMemory.editingMemoryDraft?.memoryId === String(item.memory_id)
        ? state.personalMemory.editingMemoryDraft
        : item;
      return `<form class="memory-inline-editor" data-memory-edit-form="${escapeHtml(item.memory_id)}">
        <label class="memory-field"><span>标题</span><input name="title" maxlength="120" value="${escapeHtml(draft.title || "")}"${item.title ? " required" : ""}></label>
        <label class="memory-field"><span>内容</span><textarea name="content" maxlength="4000" required>${escapeHtml(draft.content || "")}</textarea></label>
        <div class="memory-card-actions">
          <button class="memory-action" type="button" data-memory-action="cancel-edit">取消</button>
          <button class="memory-action is-primary" type="submit">保存修改</button>
        </div>
      </form>`;
    }

    function personalMemoryItemHtml(item) {
      if (!item || typeof item !== "object") return "";
      const editing = state.personalMemory.editingMemoryId === String(item.memory_id || "");
      return `<article class="memory-card" data-memory-id="${escapeHtml(item.memory_id || "")}" data-memory-version="${escapeHtml(item.version || 1)}">
        <header class="memory-card-head"><div>
          <p class="memory-card-kicker">${escapeHtml(personalMemoryKindLabel(item.kind))} · ${escapeHtml(personalMemorySourceLabel(item.source_type))}</p>
          ${editing ? "" : `<h4>${escapeHtml(item.title || item.content || "未命名记录")}</h4>`}
        </div>${editing ? "" : `<time>${escapeHtml(personalMemoryDate(item.updated_at || item.created_at))}</time>`}</header>
        ${editing ? personalMemoryEditForm(item) : `
          ${item.title ? `<p class="memory-card-content">${escapeHtml(item.content || "")}</p>` : ""}
          <p class="memory-record-meta">版本 ${escapeHtml(item.version || 1)}</p>
          ${personalMemoryEvidenceHtml(item.evidence, { sourceRef: item.source_ref })}
          <footer class="memory-card-actions">
            <button class="memory-action" type="button" data-memory-action="edit">修改</button>
            <button class="memory-action is-danger" type="button" data-memory-action="delete">删除</button>
          </footer>`}
      </article>`;
    }

    function personalMemoryCandidateEditForm(candidate) {
      const draft = state.personalMemory.editingCandidateDraft?.candidateId === String(candidate.candidate_id)
        ? state.personalMemory.editingCandidateDraft
        : { kind: candidate.proposed_kind, title: candidate.title, content: candidate.content };
      return `<form class="memory-inline-editor" data-memory-candidate-form="${escapeHtml(candidate.candidate_id || "")}">
        <label class="memory-field"><span>保存到</span><select name="kind"><option value="confirmed_fact"${draft.kind === "confirmed_fact" ? " selected" : ""}>长期信息</option><option value="research_direction"${draft.kind === "research_direction" ? " selected" : ""}>研究方向</option></select></label>
        <label class="memory-field"><span>标题</span><input name="title" maxlength="120" value="${escapeHtml(draft.title || "")}"></label>
        <label class="memory-field"><span>内容</span><textarea name="content" maxlength="4000" required>${escapeHtml(draft.content || "")}</textarea></label>
        <div class="memory-card-actions">
          <button class="memory-action" type="button" data-memory-action="cancel-candidate-edit">取消</button>
          <button class="memory-action is-primary" type="submit">确认并保存</button>
        </div>
      </form>`;
    }

    function personalMemoryCandidateHtml(candidate) {
      if (!candidate || typeof candidate !== "object") return "";
      const editing = state.personalMemory.editingCandidateId === String(candidate.candidate_id || "");
      const confidence = Number(candidate.confidence);
      return `<article class="memory-card memory-candidate-card" data-candidate-id="${escapeHtml(candidate.candidate_id || "")}" data-memory-version="${escapeHtml(candidate.version || 1)}">
        <header class="memory-card-head"><div>
          <p class="memory-card-kicker">系统推断 · 待确认 · 建议保存为${escapeHtml(personalMemoryKindLabel(candidate.proposed_kind))}</p>
          ${editing ? "" : `<h4>${escapeHtml(candidate.title || candidate.content || "未命名建议")}</h4>`}
        </div>${editing ? "" : `<time>${escapeHtml(personalMemoryDate(candidate.created_at))}</time>`}</header>
        ${editing ? personalMemoryCandidateEditForm(candidate) : `
          ${candidate.title ? `<p class="memory-card-content">${escapeHtml(candidate.content || "")}</p>` : ""}
          ${candidate.proposal_reason ? `<p class="memory-candidate-reason">为什么建议：${escapeHtml(candidate.proposal_reason)}</p>` : ""}
          <p class="memory-record-meta">${escapeHtml(personalMemorySourceLabel(candidate.source_type))}${Number.isFinite(confidence) ? ` · 系统置信度 ${escapeHtml(Math.round(confidence * 100))}%` : ""}</p>
          ${personalMemoryEvidenceHtml(candidate.evidence, { sourceRef: candidate.source_ref })}
          <footer class="memory-card-actions">
            <button class="memory-action is-primary" type="button" data-memory-action="accept-candidate">确认并记住</button>
            <button class="memory-action" type="button" data-memory-action="edit-candidate">先修改</button>
            <button class="memory-action is-danger" type="button" data-memory-action="reject-candidate">不记住</button>
          </footer>`}
      </article>`;
    }

    function personalMemoryHistorySnapshot(value) {
      if (!value || typeof value !== "object") return {};
      return value.memory || value.candidate || value;
    }

    function personalMemoryHistoryHtml(revision) {
      if (!revision || typeof revision !== "object") return "";
      const before = personalMemoryHistorySnapshot(revision.before);
      const after = personalMemoryHistorySnapshot(revision.after);
      const title = revision.title || after.title || before.title || revision.content || after.content || before.content || "记忆变更";
      const actionLabel = {
        create: "新增", update: "修改", edit: "修改", delete: "删除", restore: "恢复",
        create_from_candidate: "确认建议并新增",
        update_from_candidate: "确认建议并更新",
        restore_from_candidate: "确认建议并恢复",
        accept_candidate: "确认建议",
        reject_candidate: "否定建议",
        accept: "确认建议", reject: "否定建议", propose: "产生待确认建议",
      }[String(revision.action || "").toLowerCase()] || revision.action || "变更";
      const beforeContent = before.content || "";
      const afterContent = after.content || "";
      const comparison = beforeContent && afterContent && beforeContent !== afterContent
        ? `<details class="memory-history-compare"><summary>查看前后变化</summary><div class="memory-compare-grid"><div><span>修改前</span><p>${escapeHtml(beforeContent)}</p></div><div><span>修改后</span><p>${escapeHtml(afterContent)}</p></div></div></details>`
        : "";
      const canRestore = Boolean(revision.can_restore && revision.target_type === "memory" && Number(revision.restore_version) > 0);
      return `<article class="memory-history-card" data-memory-id="${escapeHtml(revision.target_id || "")}" data-memory-version="${escapeHtml(revision.restore_version || revision.version || 1)}">
        <header><div><span class="memory-history-action">${escapeHtml(actionLabel)}</span><h4>${escapeHtml(title)}</h4></div><time>${escapeHtml(personalMemoryDate(revision.created_at))}</time></header>
        ${revision.reason ? `<p class="memory-history-reason">${escapeHtml(revision.reason)}</p>` : ""}
        ${comparison}
        ${canRestore ? `<footer class="memory-card-actions"><button class="memory-action" type="button" data-memory-action="restore">恢复这条记忆</button></footer>` : ""}
      </article>`;
    }

    function renderPersonalMemory() {
      const memoryState = state.personalMemory;
      const dashboard = memoryState.dashboard || {};
      const countsKnown = memoryState.dashboard !== null;
      const lists = {
        confirmed: asArray(dashboard.confirmed_facts),
        research: asArray(dashboard.research_directions),
        candidates: asArray(dashboard.pending_inferences),
        history: asArray(memoryState.history),
      };
      const counts = dashboard.counts || {};
      const countValues = {
        confirmed: countsKnown ? Number(counts.confirmed_facts ?? lists.confirmed.length) : null,
        research: countsKnown ? Number(counts.research_directions ?? lists.research.length) : null,
        candidates: countsKnown ? Number(counts.pending_inferences ?? lists.candidates.length) : null,
        history: countsKnown ? Number(counts.revisions ?? lists.history.length) : null,
      };

      PERSONAL_MEMORY_TABS.forEach((tab) => {
        const selected = memoryState.activeTab === tab;
        const tabElement = document.getElementById(PERSONAL_MEMORY_TAB_IDS[tab]);
        const panel = document.getElementById(PERSONAL_MEMORY_PANEL_IDS[tab]);
        if (tabElement) {
          tabElement.classList.toggle("is-active", selected);
          tabElement.setAttribute("aria-selected", String(selected));
          tabElement.tabIndex = selected ? 0 : -1;
        }
        if (panel) panel.hidden = !selected;
        const countElement = document.getElementById({ confirmed: "memoryConfirmedCount", research: "memoryResearchCount", candidates: "memoryCandidatesCount", history: "memoryHistoryCount" }[tab]);
        if (countElement) {
          const rawCount = countValues[tab];
          if (rawCount === null || !Number.isFinite(rawCount)) {
            countElement.textContent = "—";
            countElement.setAttribute("aria-label", "尚未读取");
          } else {
            const count = Math.max(0, rawCount);
            countElement.textContent = String(count);
            countElement.setAttribute("aria-label", `${count} 条`);
          }
        }
        const listElement = document.getElementById(PERSONAL_MEMORY_LIST_IDS[tab]);
        if (listElement) {
          listElement.innerHTML = tab === "candidates"
            ? lists[tab].map(personalMemoryCandidateHtml).join("")
            : tab === "history"
              ? lists[tab].map(personalMemoryHistoryHtml).join("")
              : lists[tab].map(personalMemoryItemHtml).join("");
        }
        const emptyElement = document.getElementById({ confirmed: "memoryConfirmedEmpty", research: "memoryResearchEmpty", candidates: "memoryCandidatesEmpty", history: "memoryHistoryEmpty" }[tab]);
        if (emptyElement) emptyElement.hidden = memoryState.loadState !== "ready" || lists[tab].length > 0;
      });

      const loading = document.getElementById("memoryLoadingState");
      if (loading) loading.hidden = memoryState.loadState !== "loading";
      const error = document.getElementById("memoryErrorState");
      if (error) {
        error.hidden = memoryState.loadState !== "error";
        const message = error.querySelector("[data-memory-field='error'], p");
        if (message) message.textContent = memoryState.error || "个人记忆暂时无法读取，请重试。";
      }
      const historyMore = document.getElementById("memoryHistoryMoreBtn");
      if (historyMore) {
        historyMore.hidden = !memoryState.historyHasMore;
        historyMore.disabled = memoryState.historyLoading;
        historyMore.textContent = memoryState.historyLoading ? "正在加载…" : "加载更早记录";
      }
      document.getElementById("profilePage")?.setAttribute("aria-busy", memoryState.loadState === "loading" ? "true" : "false");
      [
        ["mobileMemoryConfirmedCount", countValues.confirmed],
        ["mobileMemoryResearchCount", countValues.research],
        ["mobileMemoryPendingCount", countValues.candidates],
      ].forEach(([id, value]) => {
        const element = document.getElementById(id);
        if (!element) return;
        if (value === null || !Number.isFinite(value)) {
          element.textContent = "—";
          element.setAttribute("aria-label", "尚未读取");
        } else {
          const count = Math.max(0, value);
          element.textContent = String(count);
          element.setAttribute("aria-label", `${count} 条`);
        }
      });
      document.querySelectorAll("#profilePage button, #profilePage input, #profilePage textarea, #profilePage select").forEach((control) => {
        if (control.id === "memoryRetryBtn") return;
        control.disabled = Boolean(memoryState.mutationBusy);
      });
      bindPersonalMemoryRenderedActions();
    }

    async function refreshPersonalMemory({ announce = false } = {}) {
      if (personalMemoryRefreshPromise) return personalMemoryRefreshPromise;
      personalMemoryRefreshPromise = refreshPersonalMemoryNow({ announce });
      try {
        return await personalMemoryRefreshPromise;
      } finally {
        personalMemoryRefreshPromise = null;
      }
    }

    async function refreshPersonalMemoryNow({ announce = false } = {}) {
      state.personalMemory.loadState = "loading";
      state.personalMemory.error = "";
      renderPersonalMemory();
      if (announce) setPersonalMemoryStatus("正在从本机读取个人记忆…");
      try {
        const dashboard = await requestJsonStrict(`${ENDPOINTS.personalMemory}?history_limit=30`, {
          cache: "no-store", timeoutMs: 15000,
        });
        state.personalMemory.dashboard = dashboard;
        state.personalMemory.history = asArray(dashboard?.history);
        state.personalMemory.historyHasMore = state.personalMemory.history.length >= 30;
        state.personalMemory.loadState = "ready";
        renderPersonalMemory();
        if (announce) setPersonalMemoryStatus("个人记忆已从本机刷新。", "success");
        return dashboard;
      } catch (error) {
        state.personalMemory.loadState = "error";
        state.personalMemory.error = configErrorMessage(error?.details) || error?.message || "个人记忆暂时无法读取。";
        renderPersonalMemory();
        setPersonalMemoryStatus(state.personalMemory.error, "error");
        throw error;
      }
    }

    async function loadMorePersonalMemoryHistory() {
      if (state.personalMemory.historyLoading || !state.personalMemory.historyHasMore) return;
      state.personalMemory.historyLoading = true;
      renderPersonalMemory();
      try {
        const limit = 50;
        const payload = await requestJsonStrict(`${ENDPOINTS.personalMemoryHistory}?limit=${limit}&offset=${state.personalMemory.history.length}`, {
          cache: "no-store", timeoutMs: 15000,
        });
        const existing = new Set(state.personalMemory.history.map((row) => String(row.revision_id)));
        const next = asArray(payload?.items).filter((row) => !existing.has(String(row?.revision_id)));
        state.personalMemory.history.push(...next);
        state.personalMemory.historyHasMore = asArray(payload?.items).length >= limit;
        setPersonalMemoryStatus(next.length ? `已加载 ${next.length} 条更早记录。` : "没有更早的记录了。", "success");
      } catch (error) {
        setPersonalMemoryStatus(configErrorMessage(error?.details) || error?.message || "更早记录加载失败。", "error");
      } finally {
        state.personalMemory.historyLoading = false;
        renderPersonalMemory();
      }
    }

    function activatePersonalMemoryTab(value, { focus = false } = {}) {
      state.personalMemory.activeTab = normalizePersonalMemoryTab(value, state.personalMemory.activeTab);
      renderPersonalMemory();
      if (focus) document.getElementById(PERSONAL_MEMORY_TAB_IDS[state.personalMemory.activeTab])?.focus();
    }

    function openPersonalMemoryPage(tab = "confirmed", { updateHash = true, focus = false, forceLoad = false } = {}) {
      if (state.newsclawMode !== true) {
        openProfilePage(tab);
        return;
      }
      const enteringPage = !personalMemoryVisible;
      leaveDesktopContentLibrary();
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("profilePage");
      personalMemoryVisible = true;
      activatePersonalMemoryTab(tab, { focus });
      if (updateHash) {
        const nextHash = `#memory/${state.personalMemory.activeTab}`;
        if (window.location.hash !== nextHash) window.location.hash = nextHash;
      }
      if (forceLoad || enteringPage || state.personalMemory.loadState === "idle" || state.personalMemory.loadState === "error") {
        void refreshPersonalMemory().catch(() => {});
      }
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    async function runPersonalMemoryMutation({ path, method = "POST", namespace, identity, payload, reconciled }) {
      if (state.personalMemory.mutationBusy) return null;
      const pending = rememberPendingRequestId(`personal-memory-${namespace}`, identity);
      state.personalMemory.mutationBusy = true;
      renderPersonalMemory();
      try {
        const result = await requestJsonStrict(path, {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...payload, request_id: pending.requestId }),
          timeoutMs: 20000,
        });
        forgetPendingRequestId(pending);
        try {
          await refreshPersonalMemory();
        } catch {
          setPersonalMemoryStatus("操作已由本机确认成功，但最新列表暂时读取失败；请点击重新读取。", "error");
        }
        return result;
      } catch (error) {
        const uncertain = error?.code === "request_timeout" || error?.name === "TimeoutError" || error instanceof TypeError;
        const code = personalMemoryErrorCode(error);
        if (uncertain) {
          setPersonalMemoryStatus("连接中断，正在核对本机记录，暂不显示假成功。", "pending");
          try {
            const dashboard = await refreshPersonalMemory();
            const confirmed = typeof reconciled === "function"
              ? await Promise.resolve(reconciled(dashboard))
              : false;
            if (confirmed) {
              forgetPendingRequestId(pending);
              setPersonalMemoryStatus("已从本机确认这次操作成功。", "success");
              return { reconciled: true };
            }
          } catch {}
          setPersonalMemoryStatus("结果暂时无法确认。页面已核账；请保留当前内容，稍后用同一操作重试。", "error");
          return null;
        }
        forgetPendingRequestId(pending);
        if (code === "personal_memory_merge_required") {
          const detail = error?.details?.detail || error?.details || {};
          return { merge_required: true, target_memory: detail.target_memory || null };
        }
        if (["personal_memory_version_conflict", "personal_memory_state_conflict"].includes(code)) {
          await refreshPersonalMemory().catch(() => {});
          setPersonalMemoryStatus("这条记忆已在别处变化，已刷新为本机最新版本，请重新确认。", "error");
          return null;
        }
        if (code === "personal_memory_request_conflict") {
          await refreshPersonalMemory().catch(() => {});
          setPersonalMemoryStatus("这次操作与上次提交内容不一致，已刷新记录，请重新操作。", "error");
          return null;
        }
        setPersonalMemoryStatus(configErrorMessage(error?.details) || error?.message || "操作失败，请重试。", "error");
        return null;
      } finally {
        state.personalMemory.mutationBusy = false;
        renderPersonalMemory();
      }
    }

    function personalMemoryItemById(memoryId) {
      const dashboard = state.personalMemory.dashboard || {};
      return [...asArray(dashboard.confirmed_facts), ...asArray(dashboard.research_directions)]
        .find((item) => String(item?.memory_id) === String(memoryId));
    }

    function personalMemoryCandidateById(candidateId) {
      return asArray(state.personalMemory.dashboard?.pending_inferences)
        .find((item) => String(item?.candidate_id) === String(candidateId));
    }

    function dashboardHasMemory(dashboard, memoryId, predicate = () => true) {
      return [...asArray(dashboard?.confirmed_facts), ...asArray(dashboard?.research_directions)]
        .some((item) => String(item?.memory_id) === String(memoryId) && predicate(item));
    }

    function dashboardHasCandidate(dashboard, candidateId) {
      return asArray(dashboard?.pending_inferences).some((item) => String(item?.candidate_id) === String(candidateId));
    }

    async function personalMemoryCandidateResolution(candidateId) {
      return requestJsonStrict(`${ENDPOINTS.personalMemory}/candidates/${encodeURIComponent(candidateId)}`, {
        cache: "no-store", timeoutMs: 15000,
      });
    }

    async function createPersonalMemory(event) {
      event.preventDefault();
      const form = event.currentTarget;
      const kind = String(document.getElementById("memoryComposerKind")?.value || "confirmed_fact");
      const title = String(document.getElementById("memoryComposerTitle")?.value || "").trim();
      const content = String(document.getElementById("memoryComposerContent")?.value || "").trim();
      const status = document.getElementById("memoryComposerStatus");
      if (!content) {
        if (status) status.textContent = "请先填写需要记住的内容。";
        document.getElementById("memoryComposerContent")?.focus();
        return;
      }
      const result = await runPersonalMemoryMutation({
        path: `${ENDPOINTS.personalMemory}/items`, namespace: "create", identity: "composer",
        payload: { kind, title, content },
        reconciled: (dashboard) => [...asArray(dashboard?.confirmed_facts), ...asArray(dashboard?.research_directions)]
          .some((item) => item.kind === kind && item.title === title && item.content === content),
      });
      if (!result) return;
      form.reset();
      form.hidden = true;
      document.getElementById("memoryCreateBtn")?.setAttribute("aria-expanded", "false");
      if (status) status.textContent = "";
      setPersonalMemoryStatus("已保存到本机个人记忆。", "success");
      openPersonalMemoryPage(kind === "research_direction" ? "research" : "confirmed");
    }

    async function editPersonalMemory(event) {
      event.preventDefault();
      const form = event.currentTarget;
      const memoryId = String(form.dataset.memoryEditForm || "");
      const item = personalMemoryItemById(memoryId);
      if (!item) return;
      const data = new FormData(form);
      const title = String(data.get("title") || "").trim();
      const content = String(data.get("content") || "").trim();
      if (!content) return;
      if (item.title && !title) {
        setPersonalMemoryStatus("已有标题不能清空；可以改成新的标题。", "error");
        return;
      }
      state.personalMemory.editingMemoryDraft = { memoryId, title, content };
      const editPayload = { expected_version: item.version, content, reason: "用户在个人记忆页修改" };
      if (title) editPayload.title = title;
      const result = await runPersonalMemoryMutation({
        path: `${ENDPOINTS.personalMemory}/items/${encodeURIComponent(memoryId)}`,
        method: "PATCH", namespace: "edit", identity: `${memoryId}:v${item.version}`,
        payload: editPayload,
        reconciled: (dashboard) => dashboardHasMemory(dashboard, memoryId, (row) => row.title === (title || item.title || "") && row.content === content && Number(row.version) > Number(item.version)),
      });
      if (result) {
        state.personalMemory.editingMemoryId = "";
        state.personalMemory.editingMemoryDraft = null;
        setPersonalMemoryStatus("修改已保存。", "success");
      }
    }

    async function acceptPersonalMemoryCandidate(event) {
      event?.preventDefault?.();
      const form = event?.currentTarget?.matches?.("form[data-memory-candidate-form]") ? event.currentTarget : null;
      const card = (form || event?.target)?.closest?.("[data-candidate-id]");
      const candidateId = String(form?.dataset.memoryCandidateForm || card?.dataset.candidateId || "");
      const candidate = personalMemoryCandidateById(candidateId);
      if (!candidate) return;
      let payload = { expected_version: candidate.version };
      if (form) {
        const data = new FormData(form);
        payload = {
          ...payload,
          kind: String(data.get("kind") || candidate.proposed_kind),
          title: String(data.get("title") || "").trim(),
          content: String(data.get("content") || "").trim(),
        };
        if (!payload.content) return;
        if (!payload.title) delete payload.title;
        state.personalMemory.editingCandidateDraft = {
          candidateId, kind: payload.kind, title: payload.title || "", content: payload.content,
        };
      }
      let result = await runPersonalMemoryMutation({
        path: `${ENDPOINTS.personalMemory}/candidates/${encodeURIComponent(candidateId)}/accept`,
        namespace: "accept", identity: `${candidateId}:v${candidate.version}`, payload,
        reconciled: async () => {
          const resolved = await personalMemoryCandidateResolution(candidateId);
          return resolved?.status === "accepted" && Boolean(resolved?.resolution_memory_id);
        },
      });
      if (result?.merge_required) {
        const target = result.target_memory;
        if (!target?.memory_id || !Number(target?.version)) {
          setPersonalMemoryStatus("已有记忆需要核对，但本机没有返回可确认的版本；请刷新后重试。", "error");
          return;
        }
        const previewText = (value, limit = 240) => {
          const normalized = String(value || "").replace(/\s+/g, " ").trim();
          return normalized.length > limit ? `${normalized.slice(0, limit - 1)}…` : normalized;
        };
        const existingTitle = previewText(target.title || "已有记忆", 100);
        const existingContent = previewText(target.content || "（无内容）");
        const proposedTitle = previewText(payload.title || candidate.title || "这条建议", 100);
        const proposedContent = previewText(payload.content || candidate.content || "（无内容）");
        const mergeMessage = [
          "这条建议会替换一条已有记忆，请核对前后内容：",
          "",
          `现有记忆｜${existingTitle}`,
          existingContent,
          "",
          `准备写入｜${proposedTitle}`,
          proposedContent,
          "",
          "确认替换吗？旧版本仍会保留在修改记录中。",
        ].join("\n");
        if (!window.confirm(mergeMessage)) {
          setPersonalMemoryStatus("已取消更新，原有记忆没有变化。", "neutral");
          return;
        }
        result = await runPersonalMemoryMutation({
          path: `${ENDPOINTS.personalMemory}/candidates/${encodeURIComponent(candidateId)}/accept`,
          namespace: "accept-merge",
          identity: `${candidateId}:v${candidate.version}:${target.memory_id}:v${target.version}`,
          payload: {
            ...payload,
            target_memory_id: target.memory_id,
            expected_memory_version: target.version,
          },
          reconciled: async () => {
            const resolved = await personalMemoryCandidateResolution(candidateId);
            return resolved?.status === "accepted"
              && String(resolved?.resolution_memory_id || "") === String(target.memory_id);
          },
        });
      }
      if (result) {
        state.personalMemory.editingCandidateId = "";
        state.personalMemory.editingCandidateDraft = null;
        setPersonalMemoryStatus("这条建议已经由你确认并保存。", "success");
      }
    }

    async function handlePersonalMemoryAction(button) {
      const action = String(button.dataset.memoryAction || "");
      const itemCard = button.closest("[data-memory-id]");
      const candidateCard = button.closest("[data-candidate-id]");
      if (action === "edit") {
        const memoryId = String(itemCard?.dataset.memoryId || "");
        const item = personalMemoryItemById(memoryId);
        state.personalMemory.editingMemoryId = memoryId;
        state.personalMemory.editingMemoryDraft = item
          ? { memoryId, title: item.title || "", content: item.content || "" }
          : null;
        renderPersonalMemory();
        return;
      }
      if (action === "cancel-edit") {
        state.personalMemory.editingMemoryId = "";
        state.personalMemory.editingMemoryDraft = null;
        renderPersonalMemory();
        return;
      }
      if (action === "edit-candidate") {
        const candidateId = String(candidateCard?.dataset.candidateId || "");
        const candidate = personalMemoryCandidateById(candidateId);
        state.personalMemory.editingCandidateId = candidateId;
        state.personalMemory.editingCandidateDraft = candidate
          ? { candidateId, kind: candidate.proposed_kind, title: candidate.title || "", content: candidate.content || "" }
          : null;
        renderPersonalMemory();
        return;
      }
      if (action === "cancel-candidate-edit") {
        state.personalMemory.editingCandidateId = "";
        state.personalMemory.editingCandidateDraft = null;
        renderPersonalMemory();
        return;
      }
      if (action === "accept-candidate") {
        await acceptPersonalMemoryCandidate({ target: button, preventDefault() {} });
        return;
      }
      if (action === "delete") {
        const memoryId = String(itemCard?.dataset.memoryId || "");
        const item = personalMemoryItemById(memoryId);
        if (!item || !window.confirm("删除这条个人记忆吗？你仍可在变更记录中恢复。")) return;
        const result = await runPersonalMemoryMutation({
          path: `${ENDPOINTS.personalMemory}/items/${encodeURIComponent(memoryId)}/delete`,
          namespace: "delete", identity: `${memoryId}:v${item.version}`,
          payload: { expected_version: item.version, reason: "用户在个人记忆页删除" },
          reconciled: (dashboard) => !dashboardHasMemory(dashboard, memoryId),
        });
        if (result) setPersonalMemoryStatus("已删除；需要时可在变更记录中恢复。", "success");
        return;
      }
      if (action === "reject-candidate") {
        const candidateId = String(candidateCard?.dataset.candidateId || "");
        const candidate = personalMemoryCandidateById(candidateId);
        if (!candidate || !window.confirm("确认这条系统推断不正确吗？否定后同一条建议不会反复出现。")) return;
        const result = await runPersonalMemoryMutation({
          path: `${ENDPOINTS.personalMemory}/candidates/${encodeURIComponent(candidateId)}/reject`,
          namespace: "reject", identity: `${candidateId}:v${candidate.version}`,
          payload: { expected_version: candidate.version, rejection_reason: "用户在个人记忆页否定" },
          reconciled: async () => {
            const resolved = await personalMemoryCandidateResolution(candidateId);
            return resolved?.status === "rejected";
          },
        });
        if (result) setPersonalMemoryStatus("已否定这条推断，不会把它写入个人记忆。", "success");
        return;
      }
      if (action === "restore") {
        const memoryId = String(itemCard?.dataset.memoryId || "");
        const expectedVersion = Number(itemCard?.dataset.memoryVersion || 0);
        if (!memoryId || expectedVersion < 1 || !window.confirm("恢复这条已删除的个人记忆吗？")) return;
        const result = await runPersonalMemoryMutation({
          path: `${ENDPOINTS.personalMemory}/items/${encodeURIComponent(memoryId)}/restore`,
          namespace: "restore", identity: `${memoryId}:v${expectedVersion}`,
          payload: { expected_version: expectedVersion, reason: "用户从变更记录恢复" },
          reconciled: (dashboard) => dashboardHasMemory(dashboard, memoryId),
        });
        if (result) setPersonalMemoryStatus("个人记忆已恢复。", "success");
      }
    }

    function bindPersonalMemoryRenderedActions() {
      document.querySelectorAll("form[data-memory-edit-form]").forEach((form) => {
        if (form.dataset.memoryBound === "1") return;
        form.dataset.memoryBound = "1";
        form.addEventListener("submit", editPersonalMemory);
      });
      document.querySelectorAll("form[data-memory-candidate-form]").forEach((form) => {
        if (form.dataset.memoryBound === "1") return;
        form.dataset.memoryBound = "1";
        form.addEventListener("submit", acceptPersonalMemoryCandidate);
      });
      document.querySelectorAll("[data-memory-action]").forEach((button) => {
        if (button.dataset.memoryBound === "1") return;
        button.dataset.memoryBound = "1";
        button.addEventListener("click", () => { void handlePersonalMemoryAction(button); });
      });
    }

    function bindPersonalMemory() {
      const buttons = PERSONAL_MEMORY_TABS.map((tab) => document.getElementById(PERSONAL_MEMORY_TAB_IDS[tab]))
        .filter((button) => button instanceof HTMLButtonElement);
      buttons.forEach((button, index) => {
        button.addEventListener("click", () => openPersonalMemoryPage(PERSONAL_MEMORY_TABS[index], { forceLoad: false }));
        button.addEventListener("keydown", (event) => {
          let nextIndex = null;
          if (event.key === "ArrowRight") nextIndex = (index + 1) % buttons.length;
          else if (event.key === "ArrowLeft") nextIndex = (index - 1 + buttons.length) % buttons.length;
          else if (event.key === "Home") nextIndex = 0;
          else if (event.key === "End") nextIndex = buttons.length - 1;
          if (nextIndex === null) return;
          event.preventDefault();
          openPersonalMemoryPage(PERSONAL_MEMORY_TABS[nextIndex], { focus: true });
        });
      });
      document.getElementById("memoryRetryBtn")?.addEventListener("click", () => { void refreshPersonalMemory({ announce: true }).catch(() => {}); });
      document.getElementById("memoryRefreshBtn")?.addEventListener("click", () => { void refreshPersonalMemory({ announce: true }).catch(() => {}); });
      document.getElementById("memoryHistoryMoreBtn")?.addEventListener("click", () => { void loadMorePersonalMemoryHistory(); });
      document.getElementById("memoryCreateBtn")?.addEventListener("click", () => {
        const form = document.getElementById("memoryComposerForm");
        if (!form) return;
        form.hidden = false;
        document.getElementById("memoryCreateBtn")?.setAttribute("aria-expanded", "true");
        document.getElementById("memoryComposerContent")?.focus();
      });
      document.getElementById("memoryComposerCancel")?.addEventListener("click", () => {
        const form = document.getElementById("memoryComposerForm");
        if (form) form.hidden = true;
        document.getElementById("memoryCreateBtn")?.setAttribute("aria-expanded", "false");
        const status = document.getElementById("memoryComposerStatus");
        if (status) status.textContent = "";
      });
      document.getElementById("memoryComposerForm")?.addEventListener("submit", createPersonalMemory);
      window.addEventListener("hashchange", () => {
        const tab = personalMemoryHashTab();
        if (tab) openPersonalMemoryPage(tab, { updateHash: false });
        else if (personalMemoryVisible) {
          leavePersonalMemory();
          if (!desktopContentLibraryHashTab()) {
            showMainPage("homePage");
            setHomeMode(state.homeMode, { persist: false });
            window.scrollTo({ top: 0, behavior: "auto" });
          }
        }
      });
      const initialTab = personalMemoryHashTab();
      if (initialTab) openPersonalMemoryPage(initialTab, { updateHash: false });
      renderPersonalMemory();
    }

    function setSideDrawerOpen(open, { persist = true } = {}) {
      const drawer = document.getElementById("sideDrawer");
      drawer?.classList.toggle("is-open", open);
      drawer?.setAttribute("aria-hidden", open ? "false" : "true");
      const button = document.getElementById("sideDrawerBtn");
      if (button) {
        button.setAttribute("aria-expanded", open ? "true" : "false");
        button.setAttribute("aria-label", open ? "收起侧边菜单" : "展开侧边菜单");
      }
      if (persist) storageSet(SIDE_DRAWER_OPEN_KEY, open ? "1" : "0");
    }

    function openSideDrawer(options) {
      setSideDrawerOpen(true, options);
    }

    function closeSideDrawer(options) {
      setSideDrawerOpen(false, options);
    }

    function toggleSideDrawer() {
      const drawer = document.getElementById("sideDrawer");
      setSideDrawerOpen(!drawer?.classList.contains("is-open"));
    }

    function isMobileViewport() {
      return window.matchMedia?.("(max-width: 820px)").matches;
    }

    function syncMobileSearch() {
      syncHomeSearch();
    }

    function openMobileMenu() {
      syncMobileSearch();
      renderRail();
      if (state.newsclawMode === true) {
        void refreshPersonalMemory().catch(() => {});
      } else if (state.newsclawMode === null) {
        void requestJson(ENDPOINTS.health).then((snapshot) => {
          const service = String(snapshot?.service || "");
          if (!service) return;
          const enabled = service === "newsclaw-api";
          setNewsClawProductMode(enabled);
          if (enabled) void refreshPersonalMemory().catch(() => {});
        }).catch(() => {});
      }
      document.body.classList.add("mobile-menu-open");
      document.getElementById("mobileMenu")?.classList.add("is-open");
    }

    function closeMobileMenu() {
      document.body.classList.remove("mobile-menu-open");
      document.getElementById("mobileMenu")?.classList.remove("is-open");
    }

    function openMobilePanel(id, options = {}) {
      closeMobileMenu();
      if (id === "messagesDrawer") {
        hydrateInboxFromSpeculations(state.profile?.speculative_interests);
        hydrateInboxFromSpeculations(state.profile?.speculative_avoidances, "avoidance.probe");
        state.messageListSnapshot = getRenderableMessages();
        returnToMessages();
        renderMessages();
        void refreshProfile().catch(() => {});
      }
      if (id === "activityDrawer") renderActivityHistory();
      const panel = document.getElementById(id);
      panel?.classList.add("from-mobile-menu");
      openPanel(id);
    }

    function openMobilePage(id, options = {}) {
      if (id === "profilePage") openProfilePage();
      if (id === "chatPage") openChatPage();
      if (id === "settingsPage") openSettingsPage(options.settingsPanel || "models");
    }

    function returnToMobileMenu(event) {
      const panel = event.target.closest(".drawer, .overlay");
      if (panel?.id) closePanel(panel.id);
      openMobileMenu();
    }

    function platformName(value) {
      return platformLabel[String(value || "").toLowerCase()] || String(value || "").trim();
    }

    // 别名只在这里归一化：引擎与库存快照只认 canonical slug。未知平台原样保留
    // （小写），这样后端新增来源时 Tab 仍能显示而不是被悄悄吞掉。
    function canonicalPlatformSlug(value) {
      const raw = String(value || "").trim().toLowerCase();
      if (!raw) return "";
      return platformAliases[raw] || raw;
    }

    function recommendationPlatformSlug(item) {
      return canonicalPlatformSlug(item?.platform ?? item?.source_platform);
    }

    function recommendationMediaLabel(item) {
      const generic = new Set(["", "news", "article", "tushare", "rss", "unknown"]);
      for (const value of [item?.media, item?.source_name, item?.publisher, item?.up]) {
        const label = String(value || "").trim();
        if (label && !generic.has(label.toLowerCase()) && label !== "媒体信息暂未提供") return label;
      }
      const fallback = platformName(item?.platform ?? item?.source_platform);
      return generic.has(String(fallback || "").trim().toLowerCase()) ? "" : fallback;
    }

    function recommendationMediaCounts() {
      const counts = new Map();
      for (const item of state.videos) {
        const label = recommendationMediaLabel(item);
        if (label) counts.set(label, (counts.get(label) || 0) + 1);
      }
      return counts;
    }

    function recommendationTopicLabel(item) {
      const explicit = String(item?.topic_group || "").replace(/\s+/g, " ").trim();
      if (explicit) return explicit;
      const tagged = normalizeDisplayTags(item?.display_tags).find((tag) => tag.type === "topic")?.label || "";
      if (tagged) return tagged;
      const legacy = String(item?.topic || "").replace(/\s+/g, " ").trim();
      return legacy && legacy !== "未归类" ? legacy : "未归类";
    }

    function recommendationTopicCounts() {
      const counts = new Map();
      for (const item of state.videos) {
        const label = recommendationTopicLabel(item);
        counts.set(label, (counts.get(label) || 0) + 1);
      }
      return counts;
    }

    // 旧的多平台 helper 依然供内容库兼容路径使用；首页的主题、媒体和
    // 搜索都只是当前已加载数据的本地视图，不是后端推荐请求参数。
    function platformSlugForFilterLabel(label) {
      const name = String(label || "").trim();
      if (!name || name === "全部") return "";
      const known = sourceFilterDefinitions.find((source) => source.label === name);
      if (known) return known.key;
      return canonicalPlatformSlug(name);
    }

    function activePlatformSlug() {
      // NewsClaw 的推荐池本身已经是 news scope；不要把界面上的媒体名
      // 伪装成 source_platform 发给后端。空字符串保留正式无范围请求形状。
      return "";
    }

    function recommendationLocalFiltersActive() {
      return state.filter !== "全部"
        || state.mediaFilter !== ALL_MEDIA_FILTER
        || Boolean(String(state.query || "").trim());
    }

    function syncReshuffleFilterGuard() {
      const button = $("#reshuffleBtn");
      if (!button) return;
      const guarded = recommendationLocalFiltersActive();
      button.setAttribute("aria-disabled", guarded ? "true" : "false");
      button.title = guarded ? "先重置本地筛选，再换一批" : "换一批";
      button.setAttribute("aria-label", button.title);
    }

    // 返回该平台「可立即推荐」的剩余数量；null = 未知（尚无成功快照）。
    // 已启用但快照里缺键的平台按 0 处理（后端允许省略零库存平台）。
    function platformAvailableCount(slug) {
      const snapshot = state.platformAvailability;
      if (!snapshot) return null;
      if (!slug) return Number(snapshot.total_available) || 0;
      const count = Number(snapshot.by_platform?.[slug]);
      return Number.isFinite(count) && count > 0 ? count : 0;
    }

    function activePlatformAvailableCount() {
      return platformAvailableCount(activePlatformSlug());
    }

    // 库存 snapshot 中数量大于 0 的平台也要出 Tab（配置里没启用、本会话也没
    // 加载过卡片时同样成立）。
    function availablePlatformSlugs() {
      const byPlatform = state.platformAvailability?.by_platform || {};
      return Object.keys(byPlatform).filter((slug) => Number(byPlatform[slug]) > 0);
    }

    // 平台定向换一批只替换该平台的卡片：其它平台本会话已加载的卡片必须原样保留，
    // 新批次插在该平台原本第一张卡的位置，保持混合列表的相对交错。
    function replacePlatformCards(videos, platform, fresh) {
      const next = [];
      let inserted = false;
      for (const item of videos) {
        if (recommendationPlatformSlug(item) === platform) {
          if (!inserted) {
            next.push(...fresh);
            inserted = true;
          }
          continue;
        }
        next.push(item);
      }
      if (!inserted) next.push(...fresh);
      return next;
    }

    // 平台定向请求返回跨平台内容 = 后端契约破坏。前端如实上报并放弃这一批，
    // 绝不静默过滤后假装成功（设计文档 §8）。
    function reportPlatformScopeLeak(action, requestPlatform, items) {
      const leaked = items.filter((item) => recommendationPlatformSlug(item) !== requestPlatform);
      if (!leaked.length) return false;
      console.error("[openbiliclaw] platform-scoped recommendation leak", {
        action,
        requested: requestPlatform,
        leaked: leaked.map((item) => ({ key: recommendationKey(item), platform: recommendationPlatformSlug(item) }))
      });
      showToast(`后端返回了 ${leaked.length} 条非「${platformName(requestPlatform)}」内容，已放弃这批${action}结果`);
      return true;
    }

    function configuredSourceFilterLabels() {
      const sources = state.config?.sources;
      const shares = state.config?.scheduler?.pool_source_shares || {};
      return sourceFilterDefinitions
        .filter(({ key }) => {
          const sourceConfig = sources?.[key];
          if (sourceConfig && typeof sourceConfig === "object" && !Array.isArray(sourceConfig) && Object.prototype.hasOwnProperty.call(sourceConfig, "enabled")) {
            return sourceConfig.enabled !== false;
          }
          return Number(shares[key] ?? 0) > 0;
        })
        .map((source) => source.label);
    }

    // 主筛选用新闻类别，而不再把媒体名当作类别。数量是当前已加载
    // 卡片数，因此无论后端库存快照是否就绪，点击都会真实过滤当前列表。
    function buildFilters() {
      const topics = [...recommendationTopicCounts()]
        .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0], "zh-Hans-CN"));
      return ["全部", ...topics.map(([label]) => label)];
    }

    function filteredVideos() {
      const q = state.query.trim().toLowerCase();
      return sortRecommendationsByPriority(state.videos.filter((item) => {
        const media = recommendationMediaLabel(item);
        const topic = recommendationTopicLabel(item);
        const topicOk = state.filter === "全部" || state.filter === topic;
        const mediaOk = state.mediaFilter === ALL_MEDIA_FILTER || state.mediaFilter === media;
        const tagText = normalizeDisplayTags(item.display_tags).map((tag) => tag.label).join(" ");
        const queryOk = !q || [
          item.title, item.up, item.media, item.provider, item.topic, item.topic_group,
          item.franchise_key, item.reason, media, tagText
        ].join(" ").toLowerCase().includes(q);
        return topicOk && mediaOk && queryOk;
      }));
    }

    function isNarrowRecommendationWorkbench() {
      return Boolean(window.matchMedia?.("(max-width: 900px)")?.matches);
    }

    function selectedRecommendation(items = filteredVideos()) {
      const selectedKey = String(state.selectedRecommendationKey || "");
      return items.find((item) => recommendationKey(item) === selectedKey) || null;
    }

    function reconcileSelectedRecommendation(items = filteredVideos()) {
      const current = selectedRecommendation(items);
      if (current) return current;
      const next = items[0] || null;
      state.selectedRecommendationKey = next ? recommendationKey(next) : "";
      if (!next) state.recommendationDetailSheetOpen = false;
      return next;
    }

    function syncRecommendationSelection(items = filteredVideos()) {
      const selected = reconcileSelectedRecommendation(items);
      const selectedKey = selected ? recommendationKey(selected) : "";
      grid?.querySelectorAll(".video-card[data-item-key]").forEach((card) => {
        const active = card.dataset.itemKey === selectedKey;
        card.classList.toggle("is-selected", active);
        if (active) card.setAttribute("aria-current", "true");
        else card.removeAttribute("aria-current");
        const selectButton = card.querySelector("[data-select-recommendation]");
        const expanded = active
          && (!isNarrowRecommendationWorkbench() || state.recommendationDetailSheetOpen);
        selectButton?.setAttribute("aria-expanded", expanded ? "true" : "false");
      });
      return selected;
    }

    function syncRecommendationDetailSheetPresentation() {
      const pane = $("#recommendationDetailPane");
      const scrim = $("#recommendationDetailScrim");
      if (!pane) return;
      const narrow = isNarrowRecommendationWorkbench();
      const open = narrow && state.recommendationDetailSheetOpen && Boolean(state.selectedRecommendationKey);
      pane.classList.toggle("is-sheet", narrow);
      pane.classList.toggle("is-open", !narrow || open);
      document.body.classList.toggle("recommendation-detail-sheet-open", open);
      if (narrow) {
        pane.setAttribute("role", "dialog");
        pane.setAttribute("aria-modal", "true");
        pane.setAttribute("aria-hidden", open ? "false" : "true");
      } else {
        pane.setAttribute("role", "region");
        pane.removeAttribute("aria-modal");
        pane.removeAttribute("aria-hidden");
      }
      if (scrim) scrim.hidden = !open;
    }

    function closeRecommendationDetailSheet({ restoreFocus = true } = {}) {
      if (!isNarrowRecommendationWorkbench()) return;
      state.recommendationDetailSheetOpen = false;
      syncRecommendationSelection();
      syncRecommendationDetailSheetPresentation();
      if (restoreFocus && recommendationDetailOpener?.isConnected) {
        recommendationDetailOpener.focus({ preventScroll: true });
      }
    }

    function selectRecommendationByKey(key, { opener = null, focusDetail = false } = {}) {
      const items = filteredVideos();
      const item = items.find((candidate) => recommendationKey(candidate) === String(key || ""));
      if (!item) return false;
      state.selectedRecommendationKey = recommendationKey(item);
      if (opener instanceof HTMLElement) recommendationDetailOpener = opener;
      if (isNarrowRecommendationWorkbench()) state.recommendationDetailSheetOpen = true;
      syncRecommendationSelection(items);
      renderRecommendationDetail(item, items);
      syncRecommendationDetailSheetPresentation();
      if (focusDetail && isNarrowRecommendationWorkbench()) {
        window.requestAnimationFrame(() => $("#recommendationDetailClose")?.focus());
      }
      return true;
    }

    function setWorkbenchView(view, { focus = false } = {}) {
      const next = view === "delight" ? "delight" : "recommendations";
      state.workbenchView = next;
      const queuePanel = $("#recommendationWorkbenchPanel");
      const delightPanel = $("#delightWorkbenchPanel");
      const queueTab = $("#workbenchQueueTab");
      const delightTab = $("#workbenchDelightTab");
      if (queuePanel) queuePanel.hidden = next !== "recommendations";
      if (delightPanel) delightPanel.hidden = next !== "delight";
      [
        [queueTab, next === "recommendations"],
        [delightTab, next === "delight"],
      ].forEach(([tab, active]) => {
        if (!tab) return;
        tab.setAttribute("aria-selected", active ? "true" : "false");
        tab.tabIndex = active ? 0 : -1;
      });
      if (focus) (next === "delight" ? delightTab : queueTab)?.focus();
      if (next === "delight") {
        state.recommendationDetailSheetOpen = false;
        _startDelightAutoAdvance();
      }
      else _stopDelightAutoAdvance();
      syncRecommendationSelection();
      syncRecommendationDetailSheetPresentation();
    }

    function normalizeThemeMode(value) {
      return THEME_OPTIONS.includes(value) ? value : "auto";
    }

    function applyThemeMode(mode = state.themeMode) {
      state.themeMode = normalizeThemeMode(mode);
      if (state.themeMode === "auto") {
        document.documentElement.removeAttribute("data-theme");
      } else {
        document.documentElement.dataset.theme = state.themeMode;
      }
      renderThemeControls();
    }

    function applyThemeHue(hue = state.themeHue) {
      state.themeHue = hue;
      document.documentElement.style.setProperty("--hue-primary", hue);
    }

    function setThemeHue(hue, { persist = true, toast = false, render = true } = {}) {
      applyThemeHue(hue);
      if (render) renderThemeHueControls();
      if (persist) storageSet(THEME_HUE_STORAGE_KEY, String(state.themeHue));
      if (toast) {
        const names = { 20: "暖陶土", 210: "极客蓝", 340: "元气粉", 150: "自然绿", 280: "暗夜紫", 45: "活力橙" };
        showToast(`主题色相已切换为${names[state.themeHue] || state.themeHue}`);
      }
    }

    function setThemeMode(mode, { persist = true, toast = false } = {}) {
      applyThemeMode(mode);
      if (persist) storageSet(THEME_STORAGE_KEY, state.themeMode);
      if (toast) showToast(`主题已切换为${THEME_LABELS[state.themeMode]}`);
    }

    function cycleThemeMode() {
      const index = THEME_OPTIONS.indexOf(normalizeThemeMode(state.themeMode));
      setThemeMode(THEME_OPTIONS[(index + 1) % THEME_OPTIONS.length], { toast: true });
    }

    function bindRovingChoiceGroup(selector, onChoose) {
      const buttons = Array.from(document.querySelectorAll(selector));
      buttons.forEach((button) => {
        button.addEventListener("click", () => onChoose(button));
        button.addEventListener("keydown", (event) => {
          if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
          const enabled = buttons.filter((candidate) => !candidate.disabled);
          if (!enabled.length) return;
          const current = Math.max(0, enabled.indexOf(button));
          let next = current;
          if (event.key === "Home") next = 0;
          else if (event.key === "End") next = enabled.length - 1;
          else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (current - 1 + enabled.length) % enabled.length;
          else next = (current + 1) % enabled.length;
          event.preventDefault();
          enabled[next].focus();
          enabled[next].click();
        });
      });
    }

    function applyAccentStyle(style = state.accentStyle) {
      state.accentStyle = ACCENT_OPTIONS.includes(style) ? style : "classic";
      if (state.accentStyle === "classic") {
        document.documentElement.dataset.accent = "classic";
      } else {
        document.documentElement.removeAttribute("data-accent");
      }
      renderThemeAccentControls();
    }

    function setAccentStyle(style, { persist = true, toast = false } = {}) {
      applyAccentStyle(style);
      if (persist) storageSet(ACCENT_STORAGE_KEY, state.accentStyle);
      if (toast) showToast(state.accentStyle === "classic" ? "已切换为经典配色，使用固定色板" : "已切换为动态主题色，可自定义色相");
    }

    function renderThemeAccentControls() {
      document.querySelectorAll("[data-accent-choice]").forEach((button) => {
        const isActive = button.dataset.accentChoice === state.accentStyle;
        button.classList.toggle("is-active", isActive);
        button.setAttribute("aria-checked", isActive ? "true" : "false");
        button.tabIndex = isActive ? 0 : -1;
      });
      const hueSection = document.querySelector(".settings-hue-field");
      if (!hueSection) return;
      const isClassic = state.accentStyle === "classic";
      hueSection.classList.toggle("is-disabled", isClassic);
      hueSection.querySelectorAll("button, input").forEach((el) => {
        if (el.matches("[data-accent-choice]")) return;
        el.disabled = isClassic;
        el.setAttribute("aria-disabled", isClassic ? "true" : "false");
      });
      const hint = hueSection.querySelector(".settings-hue-hint");
      if (hint) hint.hidden = !isClassic;
    }

    function renderThemeControls() {
      const mode = normalizeThemeMode(state.themeMode);
      const label = THEME_LABELS[mode];
      const toggle = $("#themeToggleBtn");
      if (toggle) {
        toggle.title = `主题：${label}`;
        toggle.setAttribute("aria-label", `主题：${label}`);
      }
      const glyph = $("#themeToggleGlyph");
      if (glyph) glyph.textContent = THEME_GLYPHS[mode];
      document.querySelectorAll("[data-theme-choice]").forEach((button) => {
        const isActive = button.dataset.themeChoice === mode;
        button.classList.toggle("is-active", isActive);
        button.setAttribute("aria-checked", isActive ? "true" : "false");
        button.tabIndex = isActive ? 0 : -1;
      });
    }

    function renderThemeHueControls() {
      // Number.isFinite so hue 0 (烈焰红) renders as active instead of falling back to 20.
      const hue = Number.isFinite(state.themeHue) ? state.themeHue : 20;
      const buttons = Array.from(document.querySelectorAll("[data-hue]"));
      const activeIndex = buttons.findIndex((button) => parseInt(button.dataset.hue, 10) === hue);
      buttons.forEach((button, index) => {
        const isActive = parseInt(button.dataset.hue, 10) === hue;
        button.classList.toggle("is-active", isActive);
        button.setAttribute("aria-checked", isActive ? "true" : "false");
        button.tabIndex = isActive || (activeIndex < 0 && index === 0) ? 0 : -1;
      });
      const slider = $("#hueSlider");
      if (slider) slider.value = hue;
      const hueInput = $("#hueValueInput");
      if (hueInput) hueInput.value = hue;
    }

    function setAutoLoadOnScroll(enabled, { persist = true, toast = false } = {}) {
      state.autoLoadOnScroll = Boolean(enabled);
      if (persist) storageSet(AUTO_LOAD_ON_SCROLL_KEY, state.autoLoadOnScroll ? "1" : "0");
      renderAutoLoadOnScrollToggle();
      syncAutoLoadBehavior();
      if (toast) showToast(state.autoLoadOnScroll ? "滚动到底会自动加载推荐" : "已关闭滚动自动加载");
    }

    function renderAutoLoadOnScrollToggle() {
      const toggle = $("#autoLoadOnScrollSetting");
      if (toggle && toggle.checked !== state.autoLoadOnScroll) toggle.checked = state.autoLoadOnScroll;
      const settingText = $("#autoLoadOnScrollSettingText");
      if (settingText) settingText.textContent = state.autoLoadOnScroll ? "开启" : "关闭";
    }

    function isRecommendationGridAtBottom(target, userScrolled = recommendationGridScrollIntent) {
      if (!target || !userScrolled) return false;
      const scrollHeight = Number(target.scrollHeight) || 0;
      const clientHeight = Number(target.clientHeight) || 0;
      const scrollTop = Number(target.scrollTop) || 0;
      if (clientHeight <= 0 || scrollHeight <= clientHeight + 1) return false;
      return scrollTop + clientHeight >= scrollHeight - 32;
    }

    function nextRecommendationGridScrollGate(target, gate) {
      const intent = Boolean(gate?.intent || Number(target?.scrollTop) > 0);
      const atBottom = isRecommendationGridAtBottom(target, true);
      return {
        intent,
        armed: intent && !atBottom ? true : Boolean(gate?.armed),
        atBottom,
      };
    }

    function isRecommendationGridBottomReady() {
      return recommendationGridBottomArmed && isRecommendationGridAtBottom(grid);
    }

    function refreshRecommendationGridBottomReady() {
      recommendationGridBottomReady = isRecommendationGridBottomReady();
      return recommendationGridBottomReady;
    }

    function scheduleAutoLoadCheck() {
      if (!state.autoLoadOnScroll || autoLoadCheckRaf || autoLoadCheckFallbackTimer) return;
      let settled = false;
      const run = () => {
        if (settled) return;
        settled = true;
        const rafId = autoLoadCheckRaf;
        const fallbackTimer = autoLoadCheckFallbackTimer;
        autoLoadCheckRaf = 0;
        autoLoadCheckFallbackTimer = 0;
        if (rafId && typeof cancelAnimationFrame === "function") cancelAnimationFrame(rafId);
        if (fallbackTimer) clearTimeout(fallbackTimer);
        if (!refreshRecommendationGridBottomReady()) return;
        void autoLoadMoreIfNeeded().catch(() => {});
      };
      if (typeof requestAnimationFrame === "function") {
        autoLoadCheckRaf = requestAnimationFrame(run);
        // Intersection/scroll loading is functional work, not just paint polish.
        // A backgrounded or busy browser may throttle rAF indefinitely, so keep
        // a short watchdog that runs the same coalesced geometry check once.
        autoLoadCheckFallbackTimer = setTimeout(run, 120);
      } else {
        autoLoadCheckFallbackTimer = setTimeout(run, 0);
      }
    }

    function syncAutoLoadBehavior() {
      clearAutoLoadCooldownRecheck();
      recommendationGridBottomReady = false;
      if (!state.autoLoadOnScroll) return;
      if (!grid) return;
      scheduleAutoLoadCheck();
    }

    // 库存或渲染状态变化后重新评估内层队列几何；未有真实滚动意图时 gate 仍会拦截。
    function maybeAutoLoadAfterPoolRefill() {
      scheduleAutoLoadCheck();
    }

    function recommendationAutoLoadContextReason(context) {
      if (!context.enabled) return "disabled";
      if (context.inFlight) return "in-flight";
      if (context.localFilters) return "local-filter";
      if (!context.queueView) return "not-queue-view";
      if (!context.hasStock) return "pool-empty";
      if (!context.homeVisible) return "not-home";
      if (!context.buttonVisible) return "no-button";
      if (!context.hasCards) return "no-cards";
      if (!context.cooldownReady) return "cooldown";
      return "";
    }

    // Returns the reason auto-load is currently blocked, or "" when a load
    // should proceed. The cooldown is evaluated LAST so a "cooldown" reason
    // guarantees every other precondition is already satisfied.
    function autoLoadBlockReason(now) {
      // 库存 gate 用「当前平台」的可推数量：全局还有货不代表当前 Tab 有货，否则
      // 0 库存平台会被反复空请求。库存未知（首次快照尚未成功 / 后端还没有这个
      // 接口）时回退到全局 pool_available_count，保持既有行为。
      const scopedAvailable = activePlatformAvailableCount();
      const hasStock = scopedAvailable === null
        ? state.runtimeStatus?.pool_available_count > 0
        : scopedAvailable > 0;
      const recommendationWorkspace = $("#recommendationWorkspace");
      const loadMore = $("#loadMoreBtn");
      return recommendationAutoLoadContextReason({
        enabled: state.autoLoadOnScroll,
        inFlight: appendMoreInFlight,
        localFilters: recommendationLocalFiltersActive(),
        queueView: state.workbenchView === "recommendations",
        hasStock,
        homeVisible: Boolean(recommendationWorkspace && !recommendationWorkspace.hidden),
        buttonVisible: Boolean(loadMore && !loadMore.hidden),
        hasCards: Boolean(grid.querySelector(".video-card:not(.is-skeleton)")),
        cooldownReady: now - lastAutoLoadAt >= AUTO_LOAD_COOLDOWN_MS,
      });
    }

    function shouldAutoLoadMore(now) {
      return autoLoadBlockReason(now) === "";
    }

    // 用户已真实离开并重新进入内层底部，但恰好还在 cooldown 时，只预约一次到点重检。
    function armAutoLoadCooldownRecheck(now) {
      if (autoLoadCooldownTimer) return;
      const wait = AUTO_LOAD_COOLDOWN_MS - (now - lastAutoLoadAt);
      if (wait <= 0) return;
      autoLoadCooldownTimer = setTimeout(() => {
        autoLoadCooldownTimer = 0;
        scheduleAutoLoadCheck();
      }, wait + 50);
    }

    function clearAutoLoadCooldownRecheck() {
      if (!autoLoadCooldownTimer) return;
      clearTimeout(autoLoadCooldownTimer);
      autoLoadCooldownTimer = 0;
    }

    async function autoLoadMoreIfNeeded() {
      const now = Date.now();
      const blockReason = autoLoadBlockReason(now);
      if (blockReason) {
        if (blockReason === "cooldown" && recommendationGridBottomReady) armAutoLoadCooldownRecheck(now);
        return;
      }
      lastAutoLoadAt = now;
      recommendationGridBottomArmed = false;
      const button = $("#loadMoreBtn");
      const previousText = button?.textContent || "加载更多推荐";
      if (button) {
        button.disabled = true;
        button.textContent = "正在自动加载…";
      }
      try {
        await appendMore();
      } finally {
        if (button) {
          button.disabled = false;
          button.textContent = previousText;
        }
      }
    }

    // 切换 Tab 只改变视图，不发任何推荐请求（设计文档 §5.2）。自动激活后 chip 会
    // 被重建，所以键盘路径需要把 focus 还给新的同名 chip。
    function setActiveFilter(name, { restoreFocus = false } = {}) {
      state.filter = name;
      renderAll();
      if (!restoreFocus) return;
      const chips = Array.from(document.querySelectorAll("#filterRow .chip"));
      chips.find((chip) => chip.dataset.filter === name)?.focus();
    }

    function handleFilterChipKeydown(event, filters, index) {
      const keys = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"];
      if (!keys.includes(event.key)) return;
      event.preventDefault();
      let next = index;
      if (event.key === "Home") next = 0;
      else if (event.key === "End") next = filters.length - 1;
      else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index - 1 + filters.length) % filters.length;
      else next = (index + 1) % filters.length;
      setActiveFilter(filters[next], { restoreFocus: true });
    }

    function renderMediaFilter() {
      const select = $("#mediaFilterSelect");
      if (!select) return;
      const entries = [...recommendationMediaCounts()]
        .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0], "zh-Hans-CN"));
      const available = new Set(entries.map(([label]) => label));
      if (state.mediaFilter !== ALL_MEDIA_FILTER && !available.has(state.mediaFilter)) {
        state.mediaFilter = ALL_MEDIA_FILTER;
      }
      const options = [
        [ALL_MEDIA_FILTER, `全部来源（${state.videos.length}）`],
        ...entries.map(([label, count]) => [label, `${label}（${count}）`])
      ].map(([value, text]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        return option;
      });
      select.replaceChildren(...options);
      select.value = state.mediaFilter;
    }

    function renderFilters() {
      const row = $("#filterRow");
      const filters = buildFilters();
      const topicCounts = recommendationTopicCounts();
      if (!filters.includes(state.filter)) state.filter = "全部";
      // 整排 chip 每次都被替换掉：先记住焦点原本落在哪个 Tab 上，重建后还回去，
      // 否则点一下 Tab 就把键盘焦点丢回 <body>，方向键再也走不动。
      const focusedFilter = row.contains(document.activeElement) ? String(document.activeElement.dataset.filter || "") : "";
      row.replaceChildren(...filters.map((name, index) => {
        const btn = document.createElement("button");
        const selected = state.filter === name;
        const count = name === "全部"
          ? state.videos.length
          : (topicCounts.get(name) || 0);
        btn.className = `chip${selected ? " is-active" : ""}`;
        btn.type = "button";
        btn.dataset.filter = name;
        btn.dataset.topic = name === "全部" ? "" : name;
        // role=tab + aria-selected：选中态不能只靠颜色表达，AT 也要能读到。
        btn.setAttribute("role", "tab");
        btn.setAttribute("aria-selected", selected ? "true" : "false");
        btn.setAttribute("aria-label", `${name}，当前已加载 ${count} 条`);
        btn.tabIndex = selected ? 0 : -1;
        const labelSpan = document.createElement("span");
        labelSpan.className = "chip-label";
        labelSpan.textContent = name;
        const countSpan = document.createElement("span");
        countSpan.className = "chip-count";
        countSpan.dataset.state = "known";
        countSpan.textContent = String(count);
        countSpan.setAttribute("aria-hidden", "true");
        btn.replaceChildren(labelSpan, countSpan);
        btn.addEventListener("click", () => setActiveFilter(name));
        btn.addEventListener("keydown", (event) => handleFilterChipKeydown(event, filters, index));
        return btn;
      }));
      if (focusedFilter) {
        const chips = Array.from(row.querySelectorAll(".chip"));
        const restored = chips.find((chip) => chip.dataset.filter === focusedFilter)
          || chips.find((chip) => chip.dataset.filter === state.filter);
        // 鼠标 / 触控板滚动不会清掉 Tab 焦点。自动续页重绘库存徽标时，这个
        // Tab 可能已经远在视口上方；普通 focus() 会把页面从列表底部拉回 Tab。
        restored?.focus({ preventScroll: true });
      }
      renderMediaFilter();
      const resetButton = $("#resetFiltersBtn");
      if (resetButton) {
        resetButton.hidden = !recommendationLocalFiltersActive();
      }
      syncReshuffleFilterGuard();
    }

    function normalizeImageUrl(value) {
      const url = String(value || "").trim();
      if (!url) return "";
      if (url.startsWith("//")) return `https:${url}`;
      if (url.startsWith("http://")) return `https://${url.slice("http://".length)}`;
      return url;
    }

    function imageProxyUrl(value) {
      const url = normalizeImageUrl(value);
      if (!url) return "";
      try {
        new URL(url);
      } catch {
        return "";
      }
      const base = getApiBase() || DEFAULT_API_BASE;
      // cross-origin <img> can't send the cookie/header → carry the token in the query
      return appendToken(`${base}/image-proxy?url=${encodeURIComponent(url)}`);
    }

    // In cross-origin bearer mode the cover <img> carries the token in ?token=,
    // but a plain <img> sends no Origin so the backend would ignore it. Marking
    // it crossorigin makes the browser send Origin (and skip the cookie), so the
    // allowed-origin + ?token= path authorizes it. Same-origin mode omits this so
    // the cookie is still sent. See review r4#2.
    function imgCrossOriginAttr() {
      return isCrossOriginBase() ? ' crossorigin="anonymous"' : "";
    }

    function coverImg(item, { eager = true } = {}) {
      const url = imageProxyUrl(item.cover_url);
      if (!url) return "";
      return `<img src="${escapeHtml(url)}"${imgCrossOriginAttr()} alt="${escapeHtml(item.title)} 的封面" loading="${eager ? "eager" : "lazy"}" fetchpriority="${eager ? "high" : "low"}" decoding="async" referrerpolicy="no-referrer">`;
    }

    // Warm the browser cache for a batch of cover images before their cards are
    // (re)rendered. Used by appendMore so newly loaded covers paint instantly
    // instead of flashing the placeholder while they download. Resolves on a
    // timeout so one slow cover can't stall the batch.
    const warmedCoverUrls = new Set();
    function warmCoverImages(items, { waitForDecode = false, timeoutMs = 4000 } = {}) {
      if (typeof Image === "undefined") return Promise.resolve();
      const pending = [];
      for (const item of items || []) {
        const src = imageProxyUrl(item?.cover_url);
        if (!src || warmedCoverUrls.has(src)) continue;
        warmedCoverUrls.add(src);
        const img = new Image();
        if (isCrossOriginBase()) img.crossOrigin = "anonymous";
        img.decoding = "async";
        const loaded = new Promise((resolve) => {
          img.onload = () => resolve();
          img.onerror = () => resolve();
        });
        img.src = src;
        let ready = loaded;
        if (typeof img.decode === "function") ready = img.decode().catch(() => {});
        if (waitForDecode) pending.push(ready);
      }
      if (!waitForDecode || pending.length === 0) return Promise.resolve();
      return Promise.race([Promise.all(pending), new Promise((resolve) => setTimeout(resolve, timeoutMs))]);
    }

    function safeHttpContentUrl(value) {
      const raw = String(value || "").trim();
      if (!raw) return "";
      try {
        const url = new URL(raw);
        return url.protocol === "http:" || url.protocol === "https:" ? url.href : "";
      } catch {
        return "";
      }
    }

    function contentUrl(item) {
      const platform = item.platform || item.source_platform;
      const explicitUrl = safeHttpContentUrl(item.content_url);
      if (explicitUrl) return explicitUrl;
      if (platform === "bilibili" && item.bvid) return `https://www.bilibili.com/video/${encodeURIComponent(item.bvid)}`;
      if (platform === "youtube" && item.content_id) return `https://www.youtube.com/watch?v=${encodeURIComponent(item.content_id)}`;
      if (platform === "twitter" && item.content_id) return `https://x.com/i/status/${encodeURIComponent(item.content_id)}`;
      if (platform === "bangumi" && item.content_id) return `https://bgm.tv/subject/${encodeURIComponent(item.content_id)}`;
      if (platform === "linuxdo" && item.content_id) {
        const topicId = String(item.content_id).replace(/^(?:linuxdo:)?topic[:_]/i, "");
        return /^[1-9]\d*$/.test(topicId)
          ? `https://linux.do/t/${encodeURIComponent(topicId)}`
          : "";
      }
      if (platform === "reddit") return "";
      return "";
    }

    const CONTENT_HISTORY_PAGE_SIZE = 12;
    const CONTENT_HISTORY_SECTIONS = [
      { category: "clicked", eyebrow: "Opened", title: "主动点开过", description: "你明确选择打开的内容，最近一次操作排在前面。" },
      { category: "shown", eyebrow: "Passed by", title: "出现过，但没点开", description: "曾进入推荐列表、但近 30 天没有打开记录的内容。" },
      { category: "removed", eyebrow: "Recently removed", title: "最近移除", description: "从保存列表移除、忽略或标记不感兴趣的内容。" }
    ];
    const contentHistoryState = Object.fromEntries(CONTENT_HISTORY_SECTIONS.map(({ category }) => [
      category,
      {
        items: [],
        total: 0,
        nextCursor: "",
        hasMore: false,
        loading: false,
        loadingMore: false,
        error: "",
        notice: "",
        refreshRequired: false
      }
    ]));
    let contentHistoryGeneration = 0;
    let contentHistoryLoadedAt = 0;
    const HISTORY_IMAGE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>';
    const HISTORY_RESTORE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>';

    function reconcileContentHistoryPage({
      items = [],
      incomingItems = [],
      incomingTotal = 0,
      nextCursor = "",
      hasMore = false,
      append = false
    }) {
      const current = Array.isArray(items) ? items : [];
      const incoming = Array.isArray(incomingItems) ? incomingItems : [];
      const normalizedTotal = Math.max(0, Number(incomingTotal) || 0);
      const seen = new Set();
      const merged = [];
      const addItem = (item) => {
        const itemKey = String(item?.item_key || "").trim();
        if (!itemKey || seen.has(itemKey)) return;
        seen.add(itemKey);
        merged.push(item);
      };
      if (append) current.forEach(addItem);
      incoming.forEach(addItem);
      const normalizedNextCursor = hasMore ? String(nextCursor || "").trim() : "";
      return {
        items: merged,
        total: normalizedTotal,
        nextCursor: normalizedNextCursor,
        hasMore: Boolean(hasMore && normalizedNextCursor)
      };
    }

    function contentHistoryTime(value) {
      const text = String(value || "").trim();
      if (!text) return "时间未知";
      const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(text)
        ? `${text.replace(" ", "T")}Z`
        : text;
      const date = new Date(normalized);
      if (Number.isNaN(date.getTime())) return text;
      return new Intl.DateTimeFormat("zh-CN", {
        month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"
      }).format(date);
    }

    function contentHistoryEventLabel(item, category) {
      if (category === "clicked") return "点开";
      if (category === "shown") return "出现";
      return {
        watch_later: "从稍后读移除",
        favorite: "从收藏移除",
        dismiss: "已忽略",
        dislike: "不感兴趣"
      }[item.context] || "已移除";
    }

    function contentHistoryRemovedContexts(item) {
      const contexts = Array.isArray(item?.contexts) ? item.contexts : [];
      if (contexts.length) return contexts.filter((entry) => entry && typeof entry.context === "string");
      if (!item?.context) return [];
      item.contexts = [{
        context: item.context,
        occurred_at: item.occurred_at,
        restored: item.restored === true,
        restoring: item.restoring === true
      }];
      return item.contexts;
    }

    function contentHistoryRestoreLabel(context) {
      return context === "favorite" ? "重新收藏" : "重新加入稍后";
    }

    function contentHistoryUrl(item) {
      return String(contentUrl({ ...item, bvid: item.content_id }) || "").trim();
    }

    function contentHistoryCardHtml(item, category, index) {
      const title = String(item.title || item.body_text || "这条内容暂时没有标题").trim();
      const cover = imageProxyUrl(item.cover_url);
      const media = cover
        ? `<img src="${escapeHtml(cover)}"${imgCrossOriginAttr()} alt="${escapeHtml(title)} 的封面" loading="lazy" fetchpriority="low" decoding="async" referrerpolicy="no-referrer">`
        : HISTORY_IMAGE_ICON;
      const contexts = category === "removed" ? contentHistoryRemovedContexts(item) : [];
      const contextsHtml = contexts.length
        ? `<div class="history-card-contexts" aria-label="移除原因">${contexts.map((entry) => {
          const restorable = ["watch_later", "favorite"].includes(entry.context);
          return `<div class="history-card-context">
            <span class="history-card-context-copy"><span>${escapeHtml(contentHistoryEventLabel(entry, "removed"))}</span><time>${escapeHtml(contentHistoryTime(entry.occurred_at))}</time></span>
            ${restorable ? `<button class="history-card-restore" type="button" data-history-restore="${index}" data-history-context="${escapeHtml(entry.context)}"${entry.restored ? " disabled" : entry.restoring ? ' aria-disabled="true"' : ""}>${HISTORY_RESTORE_ICON}<span>${entry.restoring ? "恢复中…" : entry.restored ? "已恢复" : contentHistoryRestoreLabel(entry.context)}</span></button>` : ""}
          </div>`;
        }).join("")}</div>`
        : "";
      return `
        <article class="history-card" data-history-item-key="${escapeHtml(item.item_key)}">
          <button class="history-card-open" type="button" data-history-open="${category}" data-history-index="${index}"${contentHistoryUrl(item) ? "" : " disabled"} aria-label="打开：${escapeHtml(title)}">
            <span class="history-card-media${cover ? "" : " is-fallback"}">${media}</span>
            <span class="history-card-copy">
              <strong class="history-card-title">${escapeHtml(title)}</strong>
              <span class="history-card-author">${escapeHtml(item.author_name || platformName(item.source_platform))}</span>
              <span class="history-card-meta"><span>${escapeHtml(category === "removed" ? `${contexts.length || 1} 项记录` : contentHistoryEventLabel(item, category))}</span><time>${escapeHtml(contentHistoryTime(item.occurred_at))}</time></span>
            </span>
          </button>
          ${contextsHtml}
        </article>`;
    }

    function contentHistorySectionHtml(section) {
      const page = contentHistoryState[section.category];
      const count = page.loading && !page.items.length ? "读取中" : `${page.total} 条`;
      let body = "";
      if (page.error && !page.items.length) {
        body = `<div class="history-empty" role="alert">${escapeHtml(page.error)}<div class="history-section-more"><button class="pill-btn" type="button" data-history-retry="${section.category}">重试</button></div></div>`;
      } else if (page.loading && !page.items.length) {
        body = '<div class="history-empty" role="status">正在整理这段历史…</div>';
      } else if (!page.items.length) {
        body = '<div class="history-empty">近 30 天还没有这类记录。</div>';
      } else {
        body = `<div class="history-section-list">${page.items.map((item, index) => contentHistoryCardHtml(item, section.category, index)).join("")}</div>`;
      }
      const message = page.items.length && (page.error || page.notice)
        ? `<p class="history-page-message ${page.error ? "is-error" : "is-notice"}" role="${page.error ? "alert" : "status"}">${escapeHtml(page.error || page.notice)}</p>`
        : "";
      const refreshingExisting = page.loading && page.items.length > 0;
      const showAction = refreshingExisting
        || page.refreshRequired
        || (page.error && page.items.length)
        || page.hasMore;
      const actionLabel = refreshingExisting
        ? "刷新中…"
        : page.loadingMore
        ? "加载中…"
        : page.refreshRequired
          ? "重试刷新列表"
          : page.error
            ? "重试加载更多"
            : "加载更多";
      const actionAttribute = page.refreshRequired || refreshingExisting
        ? "data-history-retry"
        : "data-history-more";
      const more = showAction
        ? `<div class="history-section-more"><button class="pill-btn" type="button" ${actionAttribute}="${section.category}"${page.loading || page.loadingMore ? ' aria-disabled="true"' : ""}>${actionLabel}</button></div>`
        : "";
      return `
        <section class="history-section" data-history-category="${section.category}" aria-labelledby="desktop-history-${section.category}">
          <div class="history-section-head"><div><p class="eyebrow">${escapeHtml(section.eyebrow)}</p><h3 id="desktop-history-${section.category}" tabindex="-1">${escapeHtml(section.title)}</h3></div><span class="history-section-count">${escapeHtml(count)}</span></div>
          <p class="history-section-description">${escapeHtml(section.description)}</p>
          ${body}${message}${more}
        </section>`;
    }

    function openContentHistoryItem(category, index) {
      const item = contentHistoryState[category]?.items[index];
      if (!item) return;
      const url = contentHistoryUrl(item);
      if (!url) return;
      const clickReport = trackRecommendationClick({
        ...item,
        id: item.recommendation_id,
        bvid: item.content_id,
        up: item.author_name
      });
      if (category === "shown") {
        void clickReport.then((reported) => {
          if (reported) return refreshContentHistory(true);
          return undefined;
        });
      }
      window.open(url, "_blank", "noopener,noreferrer");
    }

    function contentHistoryFocusToken(token) {
      return { ...token, scrollY: Number(window.scrollY) || 0 };
    }

    function restoreContentHistoryFocus(token, { preferAction = true } = {}) {
      if (!token) return;
      const root = document.getElementById("historySections");
      const section = [...(root?.querySelectorAll("[data-history-category]") || [])]
        .find((entry) => entry.dataset.historyCategory === token.category);
      if (!section) return;
      const card = token.itemKey
        ? [...section.querySelectorAll("[data-history-item-key]")]
          .find((entry) => entry.dataset.historyItemKey === token.itemKey)
        : null;
      let target = null;
      if (card && preferAction && token.context) {
        target = [...card.querySelectorAll("[data-history-restore]")].find((button) => (
          button.dataset.historyContext === token.context && !button.disabled
        ));
      }
      if (card && !target) {
        target = card.querySelector("[data-history-restore]:not(:disabled):not([aria-disabled='true'])")
          || card.querySelector("[data-history-open]:not(:disabled)");
      }
      if (!target && token.action) {
        target = section.querySelector(`[data-history-${token.action}]`)
          || section.querySelector("[data-history-more], [data-history-retry]");
      }
      target ||= section.querySelector("h3[tabindex='-1']");
      window.scrollTo({ top: token.scrollY, behavior: "auto" });
      target?.focus({ preventScroll: true });
      window.scrollTo({ top: token.scrollY, behavior: "auto" });
    }

    async function restoreContentHistoryItem(index, contextName) {
      const item = contentHistoryState.removed.items[index];
      const context = contentHistoryRemovedContexts(item).find((entry) => entry.context === contextName);
      if (!item || !context || context.restored || context.restoring || !["watch_later", "favorite"].includes(context.context)) return;
      const focusToken = contentHistoryFocusToken({
        category: "removed",
        itemKey: String(item.item_key || ""),
        context: context.context
      });
      context.restoring = true;
      renderContentHistory();
      restoreContentHistoryFocus(focusToken);
      let restored = false;
      try {
        await saveDesktopItem(context.context, item);
        context.restored = true;
        restored = true;
        if (item.context === context.context) item.restored = true;
        publishSavedChange(context.context, desktopSavedItem(item).item_key, true);
        showToast(context.context === "favorite" ? "已重新收藏" : "已重新加入稍后读");
        if (context.context === "favorite") void syncFavoriteButtons();
        else void syncWatchLaterButtons();
      } catch (error) {
        showToast(error?.message || "恢复失败，请稍后重试");
      } finally {
        context.restoring = false;
        renderContentHistory();
        restoreContentHistoryFocus(focusToken, { preferAction: !restored });
      }
    }

    function bindContentHistoryCards() {
      document.querySelectorAll("#historySections .history-card-media img").forEach((image) => {
        image.addEventListener("error", () => {
          const media = image.closest(".history-card-media");
          if (!media) return;
          media.classList.add("is-fallback");
          media.innerHTML = HISTORY_IMAGE_ICON;
        }, { once: true });
      });
      document.querySelectorAll("#historySections [data-history-open]").forEach((button) => {
        button.addEventListener("click", () => openContentHistoryItem(
          button.dataset.historyOpen,
          Number(button.dataset.historyIndex)
        ));
      });
      document.querySelectorAll("#historySections [data-history-restore]").forEach((button) => {
        button.addEventListener("click", () => void restoreContentHistoryItem(
          Number(button.dataset.historyRestore),
          button.dataset.historyContext
        ));
      });
      document.querySelectorAll("#historySections [data-history-more]").forEach((button) => {
        button.addEventListener("click", () => void loadContentHistoryCategory(
          button.dataset.historyMore,
          true,
          contentHistoryGeneration,
          contentHistoryFocusToken({ category: button.dataset.historyMore, action: "more" })
        ));
      });
      document.querySelectorAll("#historySections [data-history-retry]").forEach((button) => {
        button.addEventListener("click", () => void loadContentHistoryCategory(
          button.dataset.historyRetry,
          false,
          contentHistoryGeneration,
          contentHistoryFocusToken({ category: button.dataset.historyRetry, action: "retry" })
        ));
      });
    }

    function renderContentHistory() {
      const root = document.getElementById("historySections");
      if (!root) return;
      root.innerHTML = CONTENT_HISTORY_SECTIONS.map(contentHistorySectionHtml).join("");
      bindContentHistoryCards();
    }

    async function loadContentHistoryCategory(category, append, generation = contentHistoryGeneration, focusToken = null) {
      const page = contentHistoryState[category];
      if (!page || page.loading || page.loadingMore) return;
      if (append) page.loadingMore = true;
      else page.loading = true;
      page.error = "";
      page.notice = "";
      page.refreshRequired = false;
      renderContentHistory();
      restoreContentHistoryFocus(focusToken);
      try {
        const query = new URLSearchParams({
          category,
          limit: String(CONTENT_HISTORY_PAGE_SIZE)
        });
        if (append && page.nextCursor) query.set("cursor", page.nextCursor);
        const payload = await requestJsonStrict(`${ENDPOINTS.contentHistory}?${query}`, { timeoutMs: 15000 });
        if (generation !== contentHistoryGeneration) return;
        const reconciled = reconcileContentHistoryPage({
          items: page.items,
          incomingItems: payload?.items,
          incomingTotal: payload?.total,
          nextCursor: payload?.next_cursor,
          hasMore: payload?.has_more === true,
          append
        });
        page.items = reconciled.items;
        page.total = reconciled.total;
        page.nextCursor = reconciled.nextCursor;
        page.hasMore = reconciled.hasMore;
      } catch (error) {
        if (generation !== contentHistoryGeneration) return;
        page.error = error?.message || "历史记录加载失败，请稍后重试。";
      } finally {
        if (generation !== contentHistoryGeneration) return;
        page.loading = false;
        page.loadingMore = false;
        renderContentHistory();
        restoreContentHistoryFocus(focusToken);
      }
    }

    async function refreshContentHistory(force = false) {
      if (!force && contentHistoryLoadedAt && Date.now() - contentHistoryLoadedAt < 5000) return;
      contentHistoryGeneration += 1;
      const generation = contentHistoryGeneration;
      contentHistoryLoadedAt = Date.now();
      Object.values(contentHistoryState).forEach((page) => {
        page.items = [];
        page.total = 0;
        page.nextCursor = "";
        page.hasMore = false;
        page.loading = false;
        page.loadingMore = false;
        page.error = "";
        page.notice = "";
        page.refreshRequired = false;
      });
      await Promise.allSettled(CONTENT_HISTORY_SECTIONS.map(({ category }) => (
        loadContentHistoryCategory(category, false, generation)
      )));
    }

    function recommendationTextCardText(item) {
      return String(item.body_text || item.title || "先看文字也行").trim();
    }

    function recommendationHasVisualCover(item) {
      return Boolean(imageProxyUrl(item?.cover_url));
    }

    function recommendationUsesTagFingerprint(item) {
      if (recommendationHasVisualCover(item)) return false;
      const platform = recommendationPlatformSlug(item);
      return platform === "news";
    }

    function evidenceTagLabel(value) {
      const scope = String(value || "").trim().toLowerCase();
      if (scope.includes("title")) return "标题级";
      if (scope.includes("summary")) return "摘要级";
      if (scope.includes("full")) return "全文级";
      return "证据待确认";
    }

    function temporalTagLabel(item) {
      const stateLabel = {
        active: "持续有效",
        expired: "已过期",
        superseded: "已替代"
      }[String(item?.temporal_state || "").trim().toLowerCase()];
      if (stateLabel) return stateLabel;
      const classLabel = {
        breaking: "突发",
        current: "近期进展",
        versioned: "版本信息",
        historical: "历史回顾",
        evergreen: "长期有效",
        time_sensitive: "时效敏感"
      }[String(item?.temporal_class || "").trim().toLowerCase()];
      if (classLabel) return classLabel;
      const freshness = String(item?.freshness || "").replace(/\s+/g, " ").trim();
      return freshness && freshness.toLowerCase() !== "unknown"
        ? freshness.slice(0, 18)
        : "时效待确认";
    }

    function recommendationDisplayTags(item) {
      const tags = normalizeDisplayTags(item?.display_tags);
      if (tags.length >= 4) return tags.slice(0, 4);
      const candidates = [
        { type: "topic", label: recommendationTopicLabel(item) === "未归类" ? "新闻文章" : recommendationTopicLabel(item), origin: "fallback" },
        { type: "story", label: String(item?.franchise_key || "").trim(), origin: "fallback" },
        { type: "temporal", label: temporalTagLabel(item), origin: "fallback" },
        { type: "evidence", label: evidenceTagLabel(item?.content_scope), origin: "fallback" }
      ];
      const seen = new Set(tags.map((tag) => `${tag.type}\u0000${tag.label.toLocaleLowerCase("zh-Hans-CN")}`));
      const presentTypes = new Set(tags.map((tag) => tag.type));
      for (const candidate of candidates) {
        if (!candidate.label || presentTypes.has(candidate.type)) continue;
        const key = `${candidate.type}\u0000${candidate.label.toLocaleLowerCase("zh-Hans-CN")}`;
        if (seen.has(key)) continue;
        tags.push(candidate);
        seen.add(key);
        presentTypes.add(candidate.type);
        if (tags.length === 4) break;
      }
      return tags.slice(0, 4);
    }

    function recommendationTagFingerprintHtml(item) {
      const tags = recommendationDisplayTags(item);
      const tagsHtml = tags.map((tag) => `
        <span class="news-tag" data-tag-type="${escapeHtml(tag.type)}" title="${escapeHtml(`${DISPLAY_TAG_TYPE_LABELS[tag.type] || "标签"}：${tag.label}`)}">
          <span class="news-tag-kind">${escapeHtml(DISPLAY_TAG_TYPE_LABELS[tag.type] || "标签")}</span>
          <span class="news-tag-value">${escapeHtml(tag.label)}</span>
        </span>`).join("");
      return `<div class="tag-fingerprint">
        <span class="tag-fingerprint-label">新闻信息指纹</span>
        <span class="tag-fingerprint-tags">${tagsHtml}</span>
        <span class="tag-fingerprint-hint">类别 · 故事 · 时效 · 证据</span>
      </div>`;
    }

    function recommendationIsTextCard(item) {
      const hasCover = recommendationHasVisualCover(item);
      // 新闻的真实配图本身有信息量，不再被摘要文字覆盖。
      if (recommendationPlatformSlug(item) === "news" && hasCover) return false;
      return textCardContentTypes.has(String(item.content_type || "").toLowerCase()) || !hasCover;
    }

    // A text card that still carries a cover (e.g. a Zhihu answer with an
    // extracted thumbnail) renders the cover as a blurred backdrop behind the
    // excerpt — issue #79 §2: glassmorphism unifies covered and cover-less
    // cards instead of the flat gradient reading as a different visual style.
    function recommendationTextCardBackdrop(item) {
      return recommendationIsTextCard(item) ? imageProxyUrl(item.cover_url) : "";
    }

    function recommendationCoverClass(item) {
      if (recommendationUsesTagFingerprint(item)) return " is-tag-card is-coverless";
      if (!recommendationIsTextCard(item)) return "";
      return recommendationTextCardBackdrop(item)
        ? " is-text-card has-backdrop"
        : " is-text-card is-coverless";
    }

    function recommendationMediaHtml(item, index = 0) {
      const eager = index < DESKTOP_EAGER_COVER_COUNT;
      if (recommendationUsesTagFingerprint(item)) return recommendationTagFingerprintHtml(item);
      if (recommendationIsTextCard(item)) {
        const backdrop = recommendationTextCardBackdrop(item);
        const backdropHtml = backdrop
          ? `<img class="cover-backdrop" src="${escapeHtml(backdrop)}"${imgCrossOriginAttr()} alt="" aria-hidden="true" loading="${eager ? "eager" : "lazy"}" fetchpriority="${eager ? "high" : "low"}" decoding="async" referrerpolicy="no-referrer">`
          : "";
        return `${backdropHtml}<p class="cover-text">${escapeHtml(recommendationTextCardText(item))}</p>`;
      }
      return coverImg(item, { eager });
    }

    function recommendationMeta(item) {
      const media = recommendationMediaLabel(item);
      const reporter = String(item.up || "").trim();
      return [media, reporter && reporter !== media ? `记者：${reporter}` : "", item.provider ? `技术提供：${item.provider}` : "", item.topic]
        .map((part) => String(part || "").trim())
        .filter(Boolean)
        .join(" · ");
    }

    function recommendationMetaHtml(item) {
      const media = recommendationMediaLabel(item);
      const up = String(item.up || "").trim();
      const topic = String(item.topic || "").trim();
      const published = formatPublishedTime(item);
      const parts = [];
      if (media) parts.push(`<span class="news-media">${escapeHtml(media)}</span>`);
      if (up && up !== media) {
        parts.push(`记者：${escapeHtml(up)}`);
      }
      if (item.provider) parts.push(`技术提供：${escapeHtml(item.provider)}`);
      if (item.content_scope) {
        const rawScope = String(item.content_scope);
        const lowerScope = rawScope.toLowerCase();
        const scopeLabel = lowerScope.includes("title")
          ? "标题级信息"
          : lowerScope.includes("full")
            ? "全文级信息"
            : lowerScope.includes("summary")
              ? "摘要级信息"
              : rawScope;
        parts.push(`<span class="content-scope" data-scope="${escapeHtml(rawScope)}">${escapeHtml(scopeLabel)}</span>`);
      }
      if (topic && !recommendationUsesTagFingerprint(item)) parts.push(escapeHtml(topic));
      if (published) {
        const exactTitle = Number.isFinite(Date.parse(item.published_at))
          ? new Date(item.published_at).toLocaleString()
          : "";
        const title = exactTitle ? ` title="${escapeHtml(exactTitle)}"` : "";
        parts.push(`<span class="published-time"${title}>${escapeHtml(published)}</span>`);
      }
      return parts.join(" · ");
    }

    function recommendationStats(item) {
      const segments = [];
      const sourceRank = Math.trunc(Number(item.source_rank) || 0);
      if (item.view_count > 0) segments.push(`▶ ${formatCountCn(item.view_count)}`);
      if (item.like_count > 0) segments.push(`👍 ${formatCountCn(item.like_count)}`);
      if (item.comment_count > 0) segments.push(`💬 ${formatCountCn(item.comment_count)}`);
      if (item.share_count > 0) segments.push(`🔁 ${formatCountCn(item.share_count)}`);
      if (item.favorite_count > 0) segments.push(`⭐ ${formatCountCn(item.favorite_count)}`);
      if (item.rating_score > 0) segments.push(`评分 ${item.rating_score.toFixed(1)}`);
      if (item.rating_count > 0) segments.push(`${formatCountCn(item.rating_count)} 人评分`);
      if (sourceRank > 0) segments.push(`排名 #${sourceRank}`);
      return segments.join(" · ");
    }

    function makeSkeletonCard() {
      const card = document.createElement("article");
      card.className = "video-card is-skeleton";
      card.setAttribute("aria-hidden", "true");
      card.innerHTML = `
        <div class="cover skeleton-shimmer"></div>
        <div>
          <p class="video-title skeleton-line skeleton-shimmer"></p>
          <p class="video-meta skeleton-line skeleton-shimmer short"></p>
        </div>`;
      return card;
    }

    function showAppendSkeletons(count = APPEND_SKELETON_COUNT) {
      removeAppendSkeletons();
      if (grid.querySelector(".empty-state")) grid.replaceChildren();
      for (let i = 0; i < count; i += 1) grid.appendChild(makeSkeletonCard());
    }

    function removeAppendSkeletons() {
      grid.querySelectorAll(".video-card.is-skeleton").forEach((el) => el.remove());
    }

    // ---- issue #111: recommendation-style feedback actions on saved cards ----
    const SAVED_FEEDBACK_COPY = {
      like: { saving: "正在记录喜欢…", done: "已记录喜欢，会用于优化画像。", toast: "已记录喜欢" },
      dislike: { saving: "正在记录不感兴趣…", done: "已记录不感兴趣，会用于优化画像。", toast: "已记录不感兴趣" },
      dismiss: { saving: "正在记录忽略…", done: "已记录忽略，会用于优化画像。", toast: "已记录忽略" },
      comment: { saving: "正在提交聊天线索…", done: "已提交聊天线索。", toast: "已提交聊天线索" }
    };

    // Shared recommendation-card feedback action bar markup.
    function cardFeedbackBarHtml() {
      return `          <div class="card-actions" aria-label="快捷反馈与保存">
            <div class="card-feedback-icons" aria-label="喜欢或不感兴趣">
              <button class="feedback-icon-btn" data-action="like" type="button" aria-label="喜欢" title="喜欢" aria-pressed="false">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M7 10v10"/><path d="M15 5.2 14 10h5.4a1.8 1.8 0 0 1 1.7 2.2l-1.5 6A2.4 2.4 0 0 1 17.3 20H7"/><path d="M7 10l4.5-5.3A2 2 0 0 1 15 6v4"/></svg>
              </button>
              <span class="feedback-separator" aria-hidden="true">/</span>
              <button class="feedback-icon-btn" data-action="dislike" type="button" aria-label="不感兴趣" title="不感兴趣" aria-pressed="false">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M17 14V4"/><path d="M9 18.8 10 14H4.6a1.8 1.8 0 0 1-1.7-2.2l1.5-6A2.4 2.4 0 0 1 6.7 4H17"/><path d="M17 14l-4.5 5.3A2 2 0 0 1 9 18v-4"/></svg>
              </button>
              <span class="feedback-separator" aria-hidden="true">/</span>
              <button class="feedback-icon-btn" data-action="dismiss" type="button" aria-label="忽略" title="忽略" aria-pressed="false">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3l18 18M9.84 9.91A3 3 0 0 0 12 15c.82 0 1.57-.33 2.11-.87M6.5 6.65A10.45 10.45 0 0 0 2.46 12C3.73 16.06 7.52 19 12 19c1.99 0 3.84-.58 5.4-1.58M11 5.05c.33-.03.66-.05 1-.05 4.48 0 8.27 2.94 9.54 7a10.5 10.5 0 0 1-1.19 2.5"/></svg>
              </button>
              <span class="feedback-separator" aria-hidden="true">/</span>
              <button class="feedback-icon-btn watch-later-btn" data-action="watch-later" type="button" aria-label="稍后读" title="稍后读" aria-pressed="false">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3.2 1.9"/></svg>
              </button>
              <span class="feedback-separator" aria-hidden="true">/</span>
              <button class="feedback-icon-btn favorite-btn" data-action="favorite" type="button" aria-label="收藏" title="收藏" aria-pressed="false">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.6l2.65 5.37 5.93.86-4.29 4.18 1.01 5.9L12 17.1l-5.31 2.8 1.01-5.9L3.41 9.83l5.93-.86z"/></svg>
              </button>
            </div>
            <div class="comment-field"><input placeholder="写一句反馈" aria-label="写一句推荐反馈"></div>
            <button class="small-btn composer-cancel" data-action="cancel-comment" type="button" aria-label="返回" title="返回">‹</button>
            <button class="small-btn chat-action" data-action="comment" type="button">反馈一句</button>
          </div>
          <p class="status-line" aria-live="polite"></p>`;
    }

    function savedCardFeedbackBarHtml(listKind) {
      const crossIsFavorite = listKind === "watch_later";
      const crossAction = crossIsFavorite ? "favorite" : "watch-later";
      const crossClass = crossIsFavorite ? "favorite-btn" : "watch-later-btn";
      const crossLabel = crossIsFavorite ? "收藏" : "稍后读";
      const crossIcon = crossIsFavorite
        ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.6l2.65 5.37 5.93.86-4.29 4.18 1.01 5.9L12 17.1l-5.31 2.8 1.01-5.9L3.41 9.83l5.93-.86z"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3.2 1.9"/></svg>';
      return `          <div class="card-actions saved-feedback-bar" aria-label="反馈与保存操作">
            <button class="feedback-icon-btn" data-action="like" type="button" aria-label="喜欢" title="喜欢" aria-pressed="false">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M7 10v10"/><path d="M15 5.2 14 10h5.4a1.8 1.8 0 0 1 1.7 2.2l-1.5 6A2.4 2.4 0 0 1 17.3 20H7"/><path d="M7 10l4.5-5.3A2 2 0 0 1 15 6v4"/></svg>
            </button>
            <button class="feedback-icon-btn" data-action="dislike" type="button" aria-label="不感兴趣" title="不感兴趣" aria-pressed="false">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M17 14V4"/><path d="M9 18.8 10 14H4.6a1.8 1.8 0 0 1-1.7-2.2l1.5-6A2.4 2.4 0 0 1 6.7 4H17"/><path d="M17 14l-4.5 5.3A2 2 0 0 1 9 18v-4"/></svg>
            </button>
            <button class="feedback-icon-btn" data-action="saved-comment" type="button" aria-label="聊一聊" title="聊一聊">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/></svg>
            </button>
            <button class="feedback-icon-btn cross-toggle ${crossClass}" data-action="${crossAction}" type="button" aria-label="${crossLabel}" title="${crossLabel}" aria-pressed="false">
              ${crossIcon}
            </button>
          </div>
          <p class="status-line" aria-live="polite"></p>`;
    }

    // Saved-list items carry no recommendation_id, so the recommendation-scoped
    // /api/feedback (which 404s without one) cannot record like/dislike/dismiss/
    // comment for them. Mirror the extension's content-based signal path instead:
    // post a feedback behavior event to /api/events keyed on content_id, shaped
    // exactly like the recommendation feedback event (event_type=feedback +
    // metadata.feedback_type + metadata.bvid) so the soul engine treats it the same.
    function postSavedContentFeedback(item, feedbackType, note = "") {
      const saved = desktopSavedItem(item);
      const contentId = saved.content_id || item.bvid || "";
      const pending = rememberPendingRequestId(
        "behavior-command",
        JSON.stringify([saved.item_key || item.id || contentId, feedbackType, note])
      );
      return requestJsonStrict(ENDPOINTS.events, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          events: [{
            type: "feedback",
            source_platform: saved.source_platform || "news",
            title: saved.title || "",
            url: saved.content_url || "",
            timestamp: Date.now(),
            event_id: pending.requestId,
            metadata: {
              feedback_type: feedbackType,
              bvid: contentId,
              content_id: contentId,
              feedback_note: note,
              saved_feedback: true
            }
          }]
        }),
        timeoutMs: 30000
      }).then((res) => {
        if (!res || res.accepted < 1) {
          const reason = res && res.rejected && res.rejected[0] && res.rejected[0].reason;
          throw new Error(reason === "not_initialized"
            ? "画像尚未就绪，暂时无法记录反馈。"
            : "反馈未被接受，请稍后重试。");
        }
        forgetPendingRequestId(pending);
        return res;
      });
    }

    async function handleSavedCardFeedback(action, item, card) {
      const status = card.querySelector(".status-line");
      const copy = SAVED_FEEDBACK_COPY[action];
      const buttons = [...card.querySelectorAll('[data-action="like"], [data-action="dislike"], [data-action="dismiss"]')];
      const clicked = card.querySelector(`[data-action="${action}"]`);
      const snapshot = buttons.map((b) => ({ b, pressed: b.getAttribute("aria-pressed"), active: b.classList.contains("is-active") }));
      buttons.forEach((b) => { b.setAttribute("aria-pressed", "false"); b.classList.remove("is-active"); });
      if (clicked) { clicked.setAttribute("aria-pressed", "true"); clicked.classList.add("is-active"); }
      if (status) { status.removeAttribute("role"); status.textContent = copy.saving; }
      try {
        await postSavedContentFeedback(item, action, "");
        if (status) status.textContent = copy.done;
        showToast(copy.toast);
      } catch (error) {
        snapshot.forEach(({ b, pressed, active }) => {
          if (pressed == null) b.removeAttribute("aria-pressed"); else b.setAttribute("aria-pressed", pressed);
          b.classList.toggle("is-active", active);
        });
        if (status) { status.setAttribute("role", "alert"); status.textContent = error?.message || "反馈提交失败，请稍后重试。"; }
        showToast("反馈提交失败");
      }
    }

    // Saved cards expose content feedback plus only the other list's save toggle;
    // membership in the list currently being viewed is managed by 移除.
    function wireSavedCardFeedback(card, item, listKind) {
      const savedItem = desktopSavedItem(item);
      const status = card.querySelector(".status-line");
      const crossKind = listKind === "watch_later" ? "favorite" : "watch_later";

      card.querySelectorAll('[data-action="like"], [data-action="dislike"]').forEach((btn) => {
        btn.addEventListener("click", () => handleSavedCardFeedback(btn.dataset.action, item, card));
      });

      const commentBtn = card.querySelector('[data-action="saved-comment"]');
      commentBtn?.addEventListener("click", async () => {
        const draft = window.prompt("想围绕这条聊什么？");
        if (draft === null) return;
        const note = String(draft).trim();
        if (!note) {
          if (status) { status.removeAttribute("role"); status.textContent = "先写一句想聊的内容，再提交这条反馈。"; }
          return;
        }
        commentBtn.disabled = true;
        if (status) { status.removeAttribute("role"); status.textContent = SAVED_FEEDBACK_COPY.comment.saving; }
        try {
          await postSavedContentFeedback(item, "comment", note);
          if (status) status.textContent = SAVED_FEEDBACK_COPY.comment.done;
          showToast(SAVED_FEEDBACK_COPY.comment.toast);
        } catch (error) {
          if (status) { status.setAttribute("role", "alert"); status.textContent = error?.message || "反馈提交失败，请稍后重试。"; }
          showToast("反馈提交失败");
        } finally {
          commentBtn.disabled = false;
        }
      });

      const crossBtn = card.querySelector(".cross-toggle");
      if (!crossBtn) return;
      const crossIsFavorite = crossKind === "favorite";
      const setCrossState = (saved) => {
        const label = crossIsFavorite
          ? (saved ? "取消收藏" : "收藏")
          : (saved ? "取消稍后读" : "稍后读");
        crossBtn.setAttribute("aria-pressed", saved ? "true" : "false");
        crossBtn.setAttribute("aria-label", label);
        crossBtn.title = label;
      };
      crossBtn.addEventListener("click", async () => {
        if (crossBtn.disabled || desktopSavedMutations.isBusy(crossKind, savedItem.item_key)) return;
        const wasSaved = desktopSavedMutations.isSaved(crossKind, savedItem.item_key);
        crossBtn.disabled = true;
        setCrossState(!wasSaved);
        if (status) {
          status.removeAttribute("role");
          status.textContent = crossIsFavorite
            ? (wasSaved ? "正在从本地收藏移除…" : "正在保存到本地收藏…")
            : (wasSaved ? "正在从本地稍后读移除…" : "正在保存到本地稍后读…");
        }
        try {
          await desktopSavedMutations.toggle(crossKind, savedItem.item_key, {
            add: () => saveDesktopItem(crossKind, item),
            remove: () => removeDesktopSavedItem(crossKind, savedItem.item_key)
          });
          const saved = desktopSavedMutations.isSaved(crossKind, savedItem.item_key);
          setCrossState(saved);
          publishSavedChange(crossKind, savedItem.item_key, saved);
          if (status) {
            status.textContent = crossIsFavorite
              ? (saved ? "已加入本机收藏。" : "已从本机收藏移除。")
              : (saved ? "已加入本机稍后读。" : "已从本机稍后读移除。");
          }
        } catch (error) {
          setCrossState(wasSaved);
          if (status) { status.setAttribute("role", "alert"); status.textContent = error?.message || "本地保存操作失败，请重试。"; }
        } finally {
          crossBtn.disabled = false;
        }
      });
      void desktopSavedMutations.hydrate(
        crossKind,
        savedItem.item_key,
        () => savedStatus(crossKind, savedItem)
      ).then(() => setCrossState(desktopSavedMutations.isSaved(crossKind, savedItem.item_key)));
    }

    // recommendation key -> { node, html }：上一轮渲染出来的卡片。只有 markup 真的
    // 变了才会重建节点，其余情况原样复用 —— 见 syncRecommendationCards 的注释。
    const renderedRecommendationCards = new Map();

    function recommendationCardHtml(item, index) {
      const durationBadge = item.content_type === "video" && item.duration > 0
        ? `<span class="duration-badge">${escapeHtml(formatDuration(item.duration))}</span>`
        : "";
      const queueRank = String(index + 1).padStart(2, "0");
      const topic = recommendationTopicLabel(item);
      const priorityLabel = String(item.priority_label || "").trim() || `队列 ${queueRank}`;
      const priorityLevel = String(item.priority_level || "normal");
      const priorityReason = String(item.priority_reason || "").trim();
      return `
          <div class="queue-card-main">
            <div class="cover${recommendationCoverClass(item)}" data-platform="${escapeHtml(item.platform)}" aria-hidden="true">
            ${recommendationMediaHtml(item, index)}
            <span class="platform" data-platform="${escapeHtml(item.platform || "news")}">${escapeHtml(recommendationMediaLabel(item) || "新闻")}</span>
            ${durationBadge}
            </div>
            <div class="queue-card-copy">
              <div class="queue-card-flags">
                <span class="queue-priority-tag" data-priority="${escapeHtml(priorityLevel)}" title="${escapeHtml(priorityReason || priorityLabel)}">${escapeHtml(priorityLabel)}</span>
                <span class="queue-topic-tag">${escapeHtml(topic)}</span>
              </div>
              <button class="video-title queue-card-select" data-select-recommendation type="button" aria-controls="recommendationDetailPane" aria-expanded="false">${escapeHtml(item.title)}</button>
              <p class="video-meta">${recommendationMetaHtml(item)}</p>
            </div>
          </div>
${cardFeedbackBarHtml()}`;
    }

    function recommendationCardForItem(item) {
      const key = recommendationKey(item);
      return Array.from(grid?.querySelectorAll(".video-card[data-item-key]") || [])
        .find((card) => card.dataset.itemKey === key) || null;
    }

    function installRecommendationMediaFallback(container, item, index = 0) {
      const image = container?.querySelector("img:not(.cover-backdrop)");
      if (!image) return;
      image.addEventListener("error", () => {
        const fallbackItem = { ...item, cover_url: "" };
        container.className = `cover${recommendationCoverClass(fallbackItem)}`;
        container.dataset.platform = String(item.platform || item.source_platform || "news");
        container.innerHTML = `${recommendationMediaHtml(fallbackItem, index)}<span class="platform" data-platform="${escapeHtml(item.platform || "news")}">${escapeHtml(recommendationMediaLabel(item) || "新闻")}</span>`;
      }, { once: true });
    }

    function syncSavedControlsForRecommendation(item) {
      const savedItem = desktopSavedItem(item);
      if (!savedItem.item_key) return;
      const card = recommendationCardForItem(item);
      const detail = recommendationKey(selectedRecommendation()) === recommendationKey(item) ? $("#recommendationDetailPane") : null;
      [
        ["watch_later", "watch-later", "稍后读", "取消稍后读"],
        ["favorite", "favorite", "收藏", "取消收藏"],
      ].forEach(([kind, action, inactiveLabel, activeLabel]) => {
        const saved = desktopSavedMutations.isSaved(kind, savedItem.item_key);
        const buttons = [
          card?.querySelector(`[data-action="${action}"]`),
          detail?.querySelector(`[data-detail-action="${action}"]`),
        ].filter(Boolean);
        buttons.forEach((button) => {
          const label = saved ? activeLabel : inactiveLabel;
          button.setAttribute("aria-pressed", saved ? "true" : "false");
          button.setAttribute("aria-label", label);
          button.title = label;
        });
      });
    }

    function createRecommendationCard(item, html) {
      const card = document.createElement("article");
      card.className = recommendationUsesTagFingerprint(item)
        ? "video-card is-tag-only"
          : recommendationIsTextCard(item) && !recommendationTextCardBackdrop(item)
            ? "video-card is-text-only"
            : "video-card";
      card.dataset.bvid = item.bvid || item.id;
      card.dataset.itemKey = recommendationKey(item);
      card.setAttribute("role", "listitem");
      card.innerHTML = html;
      const selectButton = card.querySelector("[data-select-recommendation]");
      selectButton?.addEventListener("click", () => {
        selectRecommendationByKey(recommendationKey(item), { opener: selectButton, focusDetail: true });
      });
      selectButton?.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
        const selectors = Array.from(grid.querySelectorAll("[data-select-recommendation]"));
        const current = selectors.indexOf(selectButton);
        if (current < 0) return;
        event.preventDefault();
        const next = event.key === "ArrowDown"
          ? Math.min(selectors.length - 1, current + 1)
          : Math.max(0, current - 1);
        selectors[next]?.focus({ preventScroll: true });
      });
      const cover = card.querySelector(".cover");
      installRecommendationMediaFallback(cover, item);
      card.querySelectorAll("[data-action]").forEach((btn) => btn.addEventListener("click", () => {
        void handleCardAction(btn.dataset.action, item, card).finally(() => {
          syncSavedControlsForRecommendation(item);
          syncSelectedDetailControls();
        });
      }));
      card.querySelector(".comment-field input").addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          void handleCardAction("send-comment", item, card).finally(syncSelectedDetailControls);
        }
        if (event.key === "Escape") closeCardComposer(card);
      });
      card.querySelector(".comment-field input").addEventListener("blur", (event) => {
        autoCollapseComposer(card.querySelector(".card-actions"), event, () => closeCardComposer(card));
      });
      // Lazy-load watch-later state
      const wlBtn = card.querySelector('[data-action="watch-later"]');
      if (wlBtn) {
        const savedItem = desktopSavedItem(item);
        void desktopSavedMutations.hydrate("watch_later", savedItem.item_key, () => watchLaterStatus(savedItem)).then(() => {
          syncSavedControlsForRecommendation(item);
        });
      }
      // Lazy-load favorite state
      const favBtn = card.querySelector('[data-action="favorite"]');
      if (favBtn) {
        const savedItem = desktopSavedItem(item);
        void desktopSavedMutations.hydrate("favorite", savedItem.item_key, () => favoriteStatus(savedItem)).then(() => {
          syncSavedControlsForRecommendation(item);
        });
      }
      if (recommendationFeedbackAlreadyCommitted(item, "like")) {
        finishRecommendationFeedback(card, "like");
      }
      return card;
    }

    function trackedObservationKey(item, observation) {
      return `${recommendationKey(item)}\u0000${String(observation || "").trim()}`;
    }

    function trackedObservationList(payload) {
      if (Array.isArray(payload)) return payload;
      return asArray(payload?.items ?? payload?.records ?? payload?.tracked_observations);
    }

    function syncObservationTrackingControls(container, item) {
      if (!container || !item) return;
      container.querySelectorAll("[data-detail-track-observation]").forEach((button) => {
        const observation = String(button.dataset.observation || "").trim();
        const record = trackedObservationRecords.get(trackedObservationKey(item, observation));
        const tracked = Boolean(record) && String(record?.status || "active") === "active";
        const viewButton = button.closest?.("li")?.querySelector?.("[data-detail-view-tracking]");
        button.setAttribute("aria-pressed", tracked ? "true" : "false");
        button.textContent = tracked ? "移出跟踪" : "加入跟踪";
        button.title = tracked ? "停止在本地持续跟踪这个观察点" : "加入本地持续观察清单";
        if (viewButton) {
          viewButton.hidden = !tracked;
          viewButton.dataset.trackingId = tracked ? trackedObservationId(record) : "";
        }
      });
    }

    async function hydrateTrackedObservations(item, container) {
      const itemKey = recommendationKey(item);
      if (!itemKey || trackedObservationHydratedItems.has(itemKey)) {
        syncObservationTrackingControls(container, item);
        return;
      }
      const requestEpoch = trackedObservationEpochs.get(itemKey) || 0;
      try {
        const payload = await requestJsonStrict(
          `${ENDPOINTS.trackedObservations}?item_key=${encodeURIComponent(itemKey)}`,
          { timeoutMs: 15000 }
        );
        if ((trackedObservationEpochs.get(itemKey) || 0) !== requestEpoch) {
          syncObservationTrackingControls(container, item);
          return;
        }
        for (const [key] of trackedObservationRecords) {
          if (key.startsWith(`${itemKey}\u0000`)) trackedObservationRecords.delete(key);
        }
        trackedObservationList(payload).forEach((record) => {
          const observation = String(record?.observation || "").trim();
          if (!observation) return;
          trackedObservationRecords.set(trackedObservationKey(item, observation), record);
        });
        trackedObservationHydratedItems.add(itemKey);
      } catch {
        // 跟踪接口属于渐进增强：读取失败不影响新闻判断和其它动作，按钮仍可重试写入。
      }
      syncObservationTrackingControls(container, item);
    }

    async function toggleTrackedObservation(item, observation, button, status) {
      const cleanObservation = String(observation || "").trim();
      const itemKey = recommendationKey(item);
      if (!itemKey || !cleanObservation || !button || button.disabled) return;
      const key = trackedObservationKey(item, cleanObservation);
      const previous = trackedObservationRecords.get(key) || null;
      trackedObservationEpochs.set(itemKey, (trackedObservationEpochs.get(itemKey) || 0) + 1);
      trackedObservationHydratedItems.add(itemKey);
      button.disabled = true;
      if (status) {
        status.removeAttribute("role");
        status.textContent = previous ? "正在移出持续跟踪…" : "正在加入持续跟踪…";
      }
      try {
        if (previous) {
          const trackingId = previous.tracking_id ?? previous.id;
          if (trackingId == null || trackingId === "") throw new Error("跟踪记录缺少编号，请刷新后重试。");
          const payload = await requestJsonStrict(`${ENDPOINTS.trackedObservations}/${encodeURIComponent(trackingId)}`, {
            method: "DELETE",
            timeoutMs: 15000,
          });
          trackedObservationRecords.delete(key);
          if (payload?.item) updateTrackedObservationItem(payload.item);
          if (status) status.textContent = "已停止跟踪；已有检查记录会保留在内容库。";
          showToast("已停止持续跟踪");
        } else {
          const body = {
            request_id: newRequestId(),
            item_key: itemKey,
            observation: cleanObservation,
          };
          if (item.id != null) body.recommendation_id = item.id;
          const payload = await requestJsonStrict(ENDPOINTS.trackedObservations, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
            timeoutMs: 15000,
          });
          const record = payload?.item || payload?.record || payload;
          trackedObservationRecords.set(key, {
            ...record,
            observation: cleanObservation,
            tracking_id: record?.tracking_id ?? record?.id,
          });
          updateTrackedObservationItem(trackedObservationRecords.get(key));
          trackedObservationHydratedItems.add(itemKey);
          if (status) status.textContent = "已保存观察点。系统会在本机已采集的后续新闻里检查变化；这不代表已经发现新证据。";
          showToast("观察点已保存，可到内容库查看跟踪");
        }
        syncObservationTrackingControls(button.closest(".recommendation-detail-pane, .delight") || document, item);
        void refreshTrackedObservations({ silent: true });
      } catch (error) {
        trackedObservationHydratedItems.delete(itemKey);
        if (status) {
          status.setAttribute("role", "alert");
          status.textContent = configErrorMessage(error?.details) || error?.message || "持续跟踪操作失败，请重试。";
        }
      } finally {
        if (button.isConnected) button.disabled = false;
      }
    }

    function bindObservationTrackingControls(container, item, status) {
      container?.querySelectorAll("[data-detail-track-observation]").forEach((button) => {
        button.addEventListener("click", () => {
          void toggleTrackedObservation(item, button.dataset.observation, button, status);
        });
      });
      container?.querySelectorAll("[data-detail-view-tracking]").forEach((button) => {
        button.addEventListener("click", () => {
          const observation = String(button.dataset.observation || "").trim();
          const record = trackedObservationRecords.get(trackedObservationKey(item, observation));
          if (record) openTrackedObservationInLibrary(record);
        });
      });
      void hydrateTrackedObservations(item, container);
    }

    function recommendationDetailExplanationHtml(item) {
      const why = String(item.why_recommended || "").trim();
      const impact = String(item.impact_chain || "").trim();
      const observations = asArray(item.observation_points).map((point) => String(point || "").trim()).filter(Boolean);
      const expression = String(item.expression || item.reason || "").trim();
      const sections = [];
      if (why) {
        sections.push(`<section class="detail-analysis-block" data-analysis-field="why_recommended"><p class="detail-block-label">为什么值得看</p><p>${escapeHtml(why)}</p></section>`);
      }
      if (impact) {
        sections.push(`<section class="detail-analysis-block" data-analysis-field="impact_chain"><p class="detail-block-label">可能影响链</p><p>${escapeHtml(impact)}</p></section>`);
      }
      if (observations.length) {
        sections.push(`<section class="detail-analysis-block" data-analysis-field="observation_points"><p class="detail-block-label">后续观察点</p><ul>${observations.map((point) => `<li><span>${escapeHtml(point)}</span><span class="observation-track-actions"><button class="observation-track-btn" data-detail-track-observation data-observation="${escapeHtml(point)}" type="button" aria-pressed="false">加入跟踪</button><button class="observation-view-btn" data-detail-view-tracking data-observation="${escapeHtml(point)}" type="button" hidden>查看跟踪</button></span></li>`).join("")}</ul></section>`);
      }
      const structuredComplete = Boolean(why && impact && observations.length);
      if (!structuredComplete) {
        sections.push(`<section class="detail-analysis-block is-expression-fallback" data-analysis-field="expression"><p class="detail-block-label">${sections.length ? "后端完整推荐说明（结构字段不全）" : "推荐说明"}</p><p>${escapeHtml(expression || "后端暂未返回推荐解释。")}</p></section>`);
      }
      return sections.join("");
    }

    function recommendationDetailHtml(item, items) {
      const index = Math.max(0, items.findIndex((candidate) => recommendationKey(candidate) === recommendationKey(item)));
      const queueRank = String(index + 1).padStart(2, "0");
      const priorityLabel = String(item.priority_label || "").trim() || `队列 ${queueRank}`;
      const priorityLevel = String(item.priority_level || "normal");
      const priorityReason = String(item.priority_reason || "").trim();
      const url = contentUrl(item);
      const stats = recommendationStats(item);
      const tags = recommendationDisplayTags(item);
      const body = String(item.body_text || "").trim();
      const tagMarkup = tags.length
        ? tags.map((tag) => `<span class="detail-tag" data-tag-type="${escapeHtml(tag.type)}"><span>${escapeHtml(DISPLAY_TAG_TYPE_LABELS[tag.type] || "标签")}</span>${escapeHtml(tag.label)}</span>`).join("")
        : `<span class="detail-tag" data-tag-type="topic"><span>主题</span>${escapeHtml(recommendationTopicLabel(item))}</span>`;
      return `
        <div class="recommendation-detail-shell" data-detail-item-key="${escapeHtml(recommendationKey(item))}">
          <header class="recommendation-detail-head">
            <div class="detail-head-topline">
              <div class="queue-card-flags"><span class="queue-priority-tag" data-priority="${escapeHtml(priorityLevel)}" title="${escapeHtml(priorityReason || priorityLabel)}">${escapeHtml(priorityLabel)}</span><span class="queue-topic-tag">${escapeHtml(recommendationTopicLabel(item))}</span></div>
              ${priorityReason ? `<p class="detail-priority-reason">${escapeHtml(priorityReason)}</p>` : ""}
              <button class="icon-btn recommendation-detail-close" id="recommendationDetailClose" type="button" aria-label="关闭新闻详情">×</button>
            </div>
            <h3 id="recommendationDetailTitle" tabindex="-1" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</h3>
            <p class="video-meta">${recommendationMetaHtml(item)}</p>
            ${stats ? `<p class="video-stats">${escapeHtml(stats)}</p>` : ""}
            <div class="recommendation-detail-tags" aria-label="新闻标签">${tagMarkup}</div>
          </header>
          <div class="recommendation-detail-scroll">
            <div class="detail-media cover${recommendationCoverClass(item)}" data-platform="${escapeHtml(item.platform || "news")}">
              ${recommendationMediaHtml(item, 0)}
              <span class="platform" data-platform="${escapeHtml(item.platform || "news")}">${escapeHtml(recommendationMediaLabel(item) || "新闻")}</span>
            </div>
            <div class="detail-analysis-stack">${recommendationDetailExplanationHtml(item)}</div>
            ${body ? `<details class="detail-source-copy"><summary>查看已采集的原始内容</summary><p>${escapeHtml(body)}</p></details>` : `<p class="detail-source-missing">当前来源只提供了标题或摘要，没有可展开的正文。</p>`}
          </div>
          <footer class="recommendation-detail-actions" aria-label="当前新闻操作">
            <div class="detail-primary-actions">
              ${url
                ? `<a class="small-btn primary" data-detail-open-original href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">阅读原文</a>`
                : `<span class="small-btn is-disabled" aria-disabled="true">暂无原文链接</span>`}
              <button class="small-btn" data-detail-action="watch-later" type="button" aria-pressed="false">稍后读</button>
              <button class="small-btn" data-detail-action="favorite" type="button" aria-pressed="false">收藏</button>
            </div>
            <div class="detail-feedback-actions" aria-label="反馈这条推荐">
              <button type="button" data-detail-action="like" aria-pressed="false">喜欢</button>
              <button type="button" data-detail-action="dislike" aria-pressed="false">不感兴趣</button>
              <button type="button" data-detail-action="dismiss" aria-pressed="false">忽略</button>
              <button type="button" data-detail-undo hidden>撤销</button>
            </div>
            <form class="detail-feedback-form" data-detail-feedback-form>
              <label for="recommendationDetailFeedback">反馈一句</label>
              <div><input id="recommendationDetailFeedback" type="text" autocomplete="off" placeholder="例如：这条与我当前方向无关"><button class="small-btn" type="submit">提交反馈</button></div>
            </form>
            <p class="status-line detail-action-status" id="recommendationDetailStatus" role="status" aria-live="polite"></p>
          </footer>
        </div>`;
    }

    function syncSelectedDetailControls({ forceStatusSync = false } = {}) {
      const item = selectedRecommendation();
      const pane = $("#recommendationDetailPane");
      if (!item || !pane?.querySelector(".recommendation-detail-shell")) return;
      syncSavedControlsForRecommendation(item);
      const card = recommendationCardForItem(item);
      const feedbackType = String(item.feedback_type || "").toLowerCase();
      const pending = card?.dataset.feedbackPending === "true";
      ["like", "dislike", "dismiss"].forEach((action) => {
        const button = pane.querySelector(`[data-detail-action="${action}"]`);
        if (!button) return;
        const active = feedbackType === action;
        button.setAttribute("aria-pressed", active ? "true" : "false");
        button.classList.toggle("is-active", active);
        button.disabled = Boolean(recommendationFeedbackAlreadyCommitted(item, action) || pending);
      });
      const undo = pane.querySelector("[data-detail-undo]");
      if (undo) undo.hidden = !pending;
      const cardStatus = card?.querySelector(".status-line")?.textContent?.trim() || "";
      const detailStatus = pane.querySelector("#recommendationDetailStatus");
      if (detailStatus && (cardStatus || forceStatusSync)) detailStatus.textContent = cardStatus;
    }

    async function handleRecommendationDetailFeedback(item, note, input, submitButton) {
      const pane = input?.closest(".recommendation-detail-pane") || $("#recommendationDetailPane");
      const itemKey = recommendationKey(item);
      const isCurrentDetail = () => Boolean(
        pane?.isConnected
        && pane.dataset.detailKey === itemKey
        && recommendationKey(selectedRecommendation()) === itemKey
      );
      const status = pane?.querySelector("#recommendationDetailStatus");
      const previous = item.feedback_type;
      submitButton.disabled = true;
      if (status && isCurrentDetail()) status.textContent = "正在提交推荐反馈…";
      try {
        await submitFeedback(item, "comment", note);
        item.feedback_type = "comment";
        input.value = "";
        const cardStatus = recommendationCardForItem(item)?.querySelector(".status-line");
        if (cardStatus) cardStatus.textContent = "已提交推荐反馈，推荐会继续保留在当前列表。";
        if (status && isCurrentDetail()) status.textContent = "已提交推荐反馈，推荐会继续保留在当前列表。";
        showToast("已提交推荐反馈");
      } catch (error) {
        item.feedback_type = previous;
        if (status && isCurrentDetail()) {
          status.setAttribute("role", "alert");
          status.textContent = configErrorMessage(error?.details) || error?.message || "反馈提交失败，请稍后重试。";
        }
      } finally {
        submitButton.disabled = false;
        if (isCurrentDetail()) syncSelectedDetailControls();
      }
    }

    function renderRecommendationDetail(item = null, items = filteredVideos()) {
      const pane = $("#recommendationDetailPane");
      if (!pane) return;
      const selected = item || reconcileSelectedRecommendation(items);
      if (!selected) {
        const loading = desktopRecommendationLoadState === "loading" || desktopRecommendationLoadState === "idle";
        const loadFailed = desktopRecommendationLoadState === "failed" || desktopRecommendationLoadState === "failed-exhausted";
        const title = loading ? "正在读取推荐" : loadFailed ? "推荐暂时不可用" : state.videos.length ? "当前筛选没有结果" : "等待推荐";
        const message = loading
          ? "正在向本地后端读取推荐队列，完成前不会把未知状态显示成空列表。"
          : loadFailed
          ? "后端读取失败，但这不代表候选池为空。可以使用左侧重试按钮重新加载。"
          : state.videos.length
            ? "调整主题、媒体来源或搜索词后，第一条结果会自动成为当前判断对象。"
            : "推荐加载完成后，左侧第一条会自动成为当前判断对象。";
        pane.dataset.detailKey = "";
        pane.dataset.detailSignature = "";
        pane.innerHTML = `<div class="detail-pane-empty"><p class="eyebrow">Analysis</p><h3 id="recommendationDetailTitle">${escapeHtml(title)}</h3><p>${escapeHtml(message)}</p></div>`;
        syncRecommendationDetailSheetPresentation();
        return;
      }
      const markup = recommendationDetailHtml(selected, items);
      const signature = JSON.stringify([
        selected.id,
        selected.title,
        selected.why_recommended,
        selected.impact_chain,
        selected.observation_points,
        selected.expression,
        selected.body_text,
        selected.cover_url,
        selected.content_url,
        selected.priority,
        selected.priority_level,
        selected.priority_label,
        selected.priority_reason,
        selected.priority_signals,
        selected.display_tags,
      ]);
      const key = recommendationKey(selected);
      if (pane.dataset.detailKey === key && pane.dataset.detailSignature === signature) {
        syncSelectedDetailControls();
        syncRecommendationDetailSheetPresentation();
        return;
      }
      pane.dataset.detailKey = key;
      pane.dataset.detailSignature = signature;
      pane.innerHTML = markup;
      installRecommendationMediaFallback(pane.querySelector(".detail-media"), selected);
      pane.querySelector("#recommendationDetailClose")?.addEventListener("click", () => closeRecommendationDetailSheet());
      const originalLink = pane.querySelector("[data-detail-open-original]");
      const recordOriginalOpen = () => {
        const card = recommendationCardForItem(selected);
        const status = pane.querySelector("#recommendationDetailStatus");
        if (status) status.textContent = "原文已打开，正在记录这次主动选择…";
        const tracked = card ? openRecommendation(selected, card) : trackRecommendationClick(selected);
        void Promise.resolve(tracked).then((ok) => {
          if (!status?.isConnected || pane.dataset.detailKey !== recommendationKey(selected)) return;
          status.textContent = ok
            ? "原文已打开，这次主动选择已记录。"
            : "原文已打开，但点击信号未记录成功；不会把它宣称为已学习。";
          if (!ok) status.setAttribute("role", "alert");
        });
        syncSelectedDetailControls();
      };
      originalLink?.addEventListener("click", recordOriginalOpen);
      originalLink?.addEventListener("auxclick", (event) => {
        if (event.button === 1) recordOriginalOpen();
      });
      pane.querySelectorAll("[data-detail-action]").forEach((button) => {
        button.addEventListener("click", async () => {
          const current = selectedRecommendation();
          const card = current ? recommendationCardForItem(current) : null;
          if (!current || !card) return;
          const action = button.dataset.detailAction;
          const savedMutation = action === "watch-later" || action === "favorite";
          if (savedMutation) button.disabled = true;
          try {
            await handleCardAction(action, current, card);
          } finally {
            if (savedMutation && button.isConnected) button.disabled = false;
            syncSavedControlsForRecommendation(current);
            syncSelectedDetailControls();
          }
        });
      });
      pane.querySelector("[data-detail-undo]")?.addEventListener("click", () => {
        const current = selectedRecommendation();
        if (!current) return;
        pendingActions.undo(feedbackActionKey(current));
        window.setTimeout(() => syncSelectedDetailControls({ forceStatusSync: true }), 0);
      });
      pane.querySelector("[data-detail-feedback-form]")?.addEventListener("submit", (event) => {
        event.preventDefault();
        const input = pane.querySelector("#recommendationDetailFeedback");
        const submitButton = event.currentTarget.querySelector('button[type="submit"]');
        const note = String(input?.value || "").trim();
        if (!note) {
          const status = pane.querySelector("#recommendationDetailStatus");
          if (status) status.textContent = "先写一句反馈，再提交。";
          input?.focus();
          return;
        }
        void handleRecommendationDetailFeedback(selected, note, input, submitButton);
      });
      bindObservationTrackingControls(pane, selected, pane.querySelector("#recommendationDetailStatus"));
      syncSelectedDetailControls();
      syncRecommendationDetailSheetPresentation();
    }

    function removeRecommendationFromWorkspace(item, { broadcast = false } = {}) {
      const key = recommendationKey(item);
      if (!key) return;
      const previousLength = state.videos.length;
      state.videos = state.videos.filter((candidate) => recommendationKey(candidate) !== key);
      if (state.videos.length === previousLength) return;
      const removedSelection = state.selectedRecommendationKey === key;
      const closeNarrowSheet = removedSelection && isNarrowRecommendationWorkbench() && state.recommendationDetailSheetOpen;
      if (removedSelection) state.selectedRecommendationKey = "";
      if (closeNarrowSheet) state.recommendationDetailSheetOpen = false;
      renderAll();
      if (closeNarrowSheet) {
        window.requestAnimationFrame(() => {
          const next = selectedRecommendation();
          recommendationCardForItem(next)?.querySelector("[data-select-recommendation]")?.focus({ preventScroll: true });
        });
      }
      if (broadcast && recommendationSyncChannel) {
        recommendationSyncChannel.postMessage({ type: "recommendation-removed", item_key: key });
      }
    }

    // 按 recommendation key 做增量对账，而不是 grid.replaceChildren(...)。整表重建
    // 会把用户正在看的卡片全部销毁：浏览器丢掉滚动锚点（列表跳动）、首屏之外的
    // 懒加载封面回落成占位、展开的推荐理由和收藏 / 稍后读的 aria-pressed 复位。
    // 补货期间 refresh.pool_updated 会高频打到这条路径，所以它必须是幂等的 ——
    // 列表没变时一个 DOM 节点都不许动。
    function syncRecommendationCards(items) {
      const previous = renderedRecommendationCards;
      const next = new Map();
      // 骨架卡由 showAppendSkeletons / removeAppendSkeletons owns；这里只负责把它们
      // 保持在真实卡片后面，加载中的占位不能被一次库存重绘顺手抹掉。
      const skeletons = Array.from(grid.querySelectorAll(".video-card.is-skeleton"));
      let cursor = null;
      items.forEach((item, index) => {
        const key = recommendationKey(item) || `#${index}`;
        const html = recommendationCardHtml(item, index);
        const cached = previous.get(key);
        // 复用要求 markup 和 recommendation_id 都没变：卡片上的监听器闭包持有的是
        // 建卡时那个 item 对象，而 /api/feedback 按 recommendation_id 定位。同一条
        // 内容换了一行新推荐（换一批 / 手动刷新）时 id 会变，必须重建，否则反馈会
        // 打到已经作废的那一行上。
        const node =
          cached && cached.html === html && cached.id === item.id && cached.node.parentNode === grid
            ? cached.node
            : createRecommendationCard(item, html);
        next.set(key, { node, html, id: item.id });
        const target = cursor ? cursor.nextSibling : grid.firstChild;
        if (node !== target) grid.insertBefore(node, target);
        cursor = node;
      });
      const keep = new Set(Array.from(next.values(), (entry) => entry.node));
      for (const node of Array.from(grid.children)) {
        if (keep.has(node) || skeletons.includes(node)) continue;
        node.remove();
      }
      for (const skeleton of skeletons) grid.appendChild(skeleton);
      previous.clear();
      for (const [key, entry] of next) previous.set(key, entry);
    }

    function renderVideos() {
      if (shouldShowInitOnboarding(state.runtimeStatus)) {
        renderedRecommendationCards.clear();
        renderInitOnboarding();
        state.selectedRecommendationKey = "";
        const summary = $("#queueResultSummary");
        if (summary) summary.textContent = "需要先完成首次设置";
        renderRecommendationDetail(null, []);
        return;
      }
      const loadMore = $("#loadMoreBtn");
      if (loadMore) loadMore.hidden = false;
      const initialLoading = state.videos.length === 0
        && (desktopRecommendationLoadState === "loading" || desktopRecommendationLoadState === "idle");
      if (initialLoading) {
        renderedRecommendationCards.clear();
        grid.replaceChildren(...Array.from({ length: APPEND_SKELETON_COUNT }, () => makeSkeletonCard()));
        state.selectedRecommendationKey = "";
        if (loadMore) loadMore.disabled = true;
        const reshuffleButton = $("#reshuffleBtn");
        if (reshuffleButton) reshuffleButton.disabled = true;
        const summary = $("#queueResultSummary");
        if (summary) {
          delete summary.dataset.tone;
          summary.textContent = "正在读取推荐…";
        }
        renderRecommendationDetail(null, []);
        return;
      }
      if (loadMore && !appendMoreInFlight) loadMore.disabled = false;
      const reshuffleButton = $("#reshuffleBtn");
      if (reshuffleButton && !reshuffleInFlight) reshuffleButton.disabled = false;
      const items = filteredVideos();
      const summary = $("#queueResultSummary");
      if (summary) {
        delete summary.dataset.tone;
        const total = state.videos.length;
        summary.textContent = items.length === total
          ? `${items.length} 条待判断`
          : `显示 ${items.length} / ${total} 条`;
      }
      if (!items.length) {
        // 平台 Tab 的空态要区分「本会话还没装入」和「该平台暂时没有新候选」，
        // 用的是同一份可推库存快照，不是 DOM 卡片数。
        const activePlatform = activePlatformSlug();
        const activePlatformCount = platformAvailableCount(activePlatform);
        const loadFailed = desktopRecommendationLoadState === "failed" || desktopRecommendationLoadState === "failed-exhausted";
        const platformMessage = activePlatform && !loadFailed
          ? activePlatformCount === null
            ? `${escapeHtml(platformName(activePlatform))}还没有装入推荐，可以点「加载更多推荐」试试。`
            : activePlatformCount > 0
              ? `${escapeHtml(platformName(activePlatform))}还有 ${activePlatformCount} 条候选没装进来，点「加载更多推荐」即可。`
              : `${escapeHtml(platformName(activePlatform))}暂时没有新候选，后台会继续补货。`
          : "";
        const hasLocalFilter = state.filter !== "全部" || state.mediaFilter !== ALL_MEDIA_FILTER;
        const message = state.query.trim()
          ? `没有找到包含“${escapeHtml(state.query.trim())}”的推荐。`
          : hasLocalFilter
            ? "当前类别或媒体来源下没有已加载的推荐，可以重置筛选或继续加载。"
          : platformMessage
            ? platformMessage
            : state.videos.length
              ? "当前筛选下没有推荐。"
              : desktopRecommendationLoadState === "failed"
                ? "推荐加载失败，正在重试；这不代表候选池真的为空。"
                : desktopRecommendationLoadState === "failed-exhausted"
                  ? "推荐加载失败，点一下重新加载。"
                  : "当前列表里的推荐都已处理，可以加载更多推荐或等待后端补货。";
        const retry = desktopRecommendationLoadState === "failed-exhausted"
          ? '<button class="small-btn" id="retryEmptyRecommendations" type="button">重新加载</button>'
          : "";
        renderedRecommendationCards.clear();
        grid.innerHTML = `<div class="empty-state">${message}${retry}</div>`;
        $("#retryEmptyRecommendations")?.addEventListener("click", restartDesktopFailedRecoveries);
        state.selectedRecommendationKey = "";
        renderRecommendationDetail(null, items);
        return;
      }
      reconcileSelectedRecommendation(items);
      // The initial loading skeletons share the same class as append placeholders.
      // Once the first real response arrives there is no append owner to remove them,
      // so keeping them here leaves four blank cards in the queue and distorts its
      // scroll height. During a real append request the placeholders stay until that
      // request's finally block removes them.
      if (!appendMoreInFlight) removeAppendSkeletons();
      syncRecommendationCards(items);
      syncRecommendationSelection(items);
      renderRecommendationDetail(selectedRecommendation(items), items);
    }

    function trackRecommendationClick(item) {
      const url = contentUrl(item);
      const payload = {
          bvid: item.bvid,
          content_id: item.content_id || item.bvid,
          content_url: url || item.content_url,
          source_platform: item.platform || item.source_platform,
          title: item.title,
          recommendation_id: item.id,
          topic_label: item.topic,
          up_name: item.up || item.up_name
      };
      const stableRecommendationId = payload.recommendation_id ?? null;
      const stableContentId = String(payload.content_id || payload.bvid || "").trim();
      let fallbackUrl = "";
      if (stableRecommendationId == null && !stableContentId) {
        const rawUrl = String(payload.content_url || "").trim();
        try {
          const normalizedUrl = new URL(rawUrl, globalThis.location?.href);
          normalizedUrl.hash = "";
          fallbackUrl = normalizedUrl.toString();
        } catch { fallbackUrl = rawUrl; }
      }
      const identity = JSON.stringify([
        stableRecommendationId,
        stableContentId || fallbackUrl
      ]);
      return requestJsonWithPendingId(
        ENDPOINTS.click,
        "recommendation-click",
        identity,
        payload
      ).then(() => true, () => false);
    }

    async function openRecommendation(item, card) {
      const url = contentUrl(item);
      const status = card?.querySelector(".status-line");
      if (status) status.textContent = url ? "已打开原文，正在记录这次主动选择…" : "没有可打开的安全原文链接，正在尝试记录这次选择…";
      showToast(url ? `打开：${item.title}` : "后端没有返回可打开链接");
      const recorded = await trackRecommendationClick(item);
      if (status?.isConnected) {
        status.textContent = recorded
          ? (url ? "已打开原文，这次主动选择已记录。" : "没有可打开链接；选择信号已记录。")
          : (url ? "原文已打开，但点击信号未记录成功。" : "没有可打开链接，点击信号也未记录成功。");
        if (!recorded) status.setAttribute("role", "alert");
      }
      return recorded;
    }

    function submitFeedback(item, feedback_type, note = "", { keepalive = false } = {}) {
      const payload = { recommendation_id: item.id, feedback_type, note };
      return requestJsonWithPendingId(
        ENDPOINTS.feedback,
        "feedback",
        JSON.stringify([item.id, feedback_type, note]),
        payload,
        {
        timeoutMs: 30000,
        keepalive
        }
      );
    }

    function feedbackActionKey(item) {
      const contentId = item?.bvid || item?.content_id;
      if (!contentId) return "";
      const platform = String(item?.platform || item?.source_platform || "").trim().toLowerCase();
      return `recommendation:${platform}:${contentId}`;
    }

    function recommendationFeedbackButtons(card) {
      return [...card.querySelectorAll('[data-action="like"], [data-action="dislike"], [data-action="dismiss"]')];
    }

    function recommendationFeedbackSnapshot(item, card, status) {
      return {
        feedbackType: item.feedback_type,
        statusText: status.textContent,
        pending: card.dataset.feedbackPending,
        buttons: recommendationFeedbackButtons(card).map((button) => ({
          button,
          disabled: button.disabled,
          pressed: button.getAttribute("aria-pressed"),
          active: button.classList.contains("is-active")
        }))
      };
    }

    function restoreRecommendationFeedback(item, card, status, snapshot) {
      item.feedback_type = snapshot.feedbackType;
      status.classList.remove("has-feedback-action");
      status.textContent = snapshot.statusText;
      card.classList.remove("is-feedback-pending", "is-feedback-saving");
      if (snapshot.pending == null) delete card.dataset.feedbackPending;
      else card.dataset.feedbackPending = snapshot.pending;
      snapshot.buttons.forEach(({ button, disabled, pressed, active }) => {
        button.disabled = disabled;
        if (pressed == null) button.removeAttribute("aria-pressed");
        else button.setAttribute("aria-pressed", pressed);
        button.classList.toggle("is-active", active);
      });
    }

    function stageRecommendationFeedback(item, card, feedbackType) {
      const key = feedbackActionKey(item);
      if (!key) {
        showToast("这条推荐缺少稳定内容标识，暂时无法记录反馈。");
        return false;
      }
      const status = card.querySelector(".status-line");
      const snapshot = recommendationFeedbackSnapshot(item, card, status);
      const copy = {
        like: {
          pending: "已标记喜欢，10 秒内可撤销。",
          saving: "正在保存喜欢反馈…",
          committed: "已记录喜欢，推荐会继续保留在当前列表。",
          toast: "已记录喜欢"
        },
        dislike: {
          pending: "已标记不感兴趣，10 秒内可撤销。",
          saving: "正在保存不感兴趣反馈…",
          committed: "已记录不感兴趣，并从当前队列移除。",
          toast: "已记录不感兴趣"
        },
        dismiss: {
          pending: "已标记忽略，10 秒内可撤销。",
          saving: "正在保存忽略反馈…",
          committed: "已忽略这条推荐，并从当前队列移除。",
          toast: "已忽略推荐"
        }
      }[feedbackType];
      const scheduled = pendingActions.schedule(key, {
        commit: ({ keepalive }) => {
          if (card.isConnected && !keepalive) {
            card.classList.remove("is-feedback-pending");
            card.classList.add("is-feedback-saving");
            status.classList.remove("has-feedback-action");
            status.textContent = copy.saving;
            syncSelectedDetailControls({ forceStatusSync: true });
          }
          return submitFeedback(item, feedbackType, "", { keepalive });
        },
        rollback: ({ reason }) => {
          restoreRecommendationFeedback(item, card, status, snapshot);
          syncSelectedDetailControls({ forceStatusSync: true });
          if (reason === "undo") showToast("已撤销反馈");
        },
        committed: () => {
          if (card.isConnected) {
            delete card.dataset.feedbackPending;
            card.classList.remove("is-feedback-pending", "is-feedback-saving");
            status.classList.remove("has-feedback-action");
            status.textContent = copy.committed;
          }
          showToast(copy.toast);
          if (shouldRemoveRecommendationAfterFeedback(feedbackType)) {
            removeRecommendationFromWorkspace(item, { broadcast: true });
          } else {
            syncSelectedDetailControls({ forceStatusSync: true });
          }
        }
      });
      if (!scheduled) return false;

      item.feedback_type = feedbackType;
      card.dataset.feedbackPending = "true";
      card.classList.add("is-feedback-pending");
      const clicked = card.querySelector(`[data-action="${feedbackType}"]`);
      recommendationFeedbackButtons(card).forEach((button) => { button.disabled = true; });
      if (clicked) {
        clicked.setAttribute("aria-pressed", "true");
        clicked.classList.add("is-active");
      }
      const undo = document.createElement("button");
      undo.type = "button";
      undo.className = "feedback-undo-btn";
      undo.dataset.feedbackUndo = key;
      undo.textContent = "撤销";
      undo.addEventListener("click", () => { pendingActions.undo(key); });
      status.classList.add("has-feedback-action");
      status.setAttribute("aria-live", "polite");
      status.replaceChildren(document.createTextNode(`${copy.pending} `), undo);
      syncSelectedDetailControls({ forceStatusSync: true });
      return true;
    }

    function finishRecommendationFeedback(card, feedbackType = "") {
      if (!card) return;
      delete card.dataset.feedbackPending;
      card.querySelectorAll(".card-actions button, .card-actions input").forEach((control) => { control.disabled = false; });
      const normalized = String(feedbackType || "").trim().toLowerCase();
      if (normalized !== "like") return;
      const button = card.querySelector('[data-action="like"]');
      if (!button) return;
      button.setAttribute("aria-pressed", "true");
      button.classList.add("is-active");
      button.disabled = true;
    }

    const sendIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M4 12 20 4l-5 16-3.2-6.8L4 12Z"/><path d="m11.8 13.2 3.7-3.7"/></svg>';

    function openCardComposer(card) {
      const actions = card.querySelector(".card-actions");
      const button = card.querySelector(".chat-action");
      actions.classList.add("is-composing");
      button.classList.add("is-send");
      button.dataset.action = "send-comment";
      button.innerHTML = sendIcon;
      button.setAttribute("aria-label", "发送反馈");
      button.setAttribute("title", "发送反馈");
      requestAnimationFrame(() => card.querySelector(".comment-field input")?.focus());
    }

    function closeCardComposer(card) {
      const actions = card.querySelector(".card-actions");
      const button = card.querySelector(".chat-action");
      actions.classList.remove("is-composing");
      button.classList.remove("is-send");
      button.dataset.action = "comment";
      button.textContent = "反馈一句";
      button.removeAttribute("aria-label");
      button.removeAttribute("title");
    }

    // Collapse an open composer back to the 聊一聊 button when focus leaves it
    // (user clicked 聊一聊 then changed their mind). The typed draft stays in the
    // input, so reopening restores it. Deferred so a click on the send / cancel
    // button — which blurs the input first in some browsers — still wins.
    function autoCollapseComposer(container, event, closeFn) {
      if (!container || !container.classList.contains("is-composing")) return;
      const next = event.relatedTarget;
      if (next && container.contains(next)) return;
      window.setTimeout(() => {
        if (!container.classList.contains("is-composing")) return;
        if (container.contains(document.activeElement)) return;
        closeFn();
      }, 120);
    }

    function openDelightComposer() {
      const actions = document.querySelector(".delight-main-actions");
      const shell = actions?.closest(".delight-actions");
      const button = actions?.querySelector(".chat-action");
      if (!actions || !button || !state.delight) return;
      shell?.classList.add("is-composing");
      actions.classList.add("is-composing");
      button.classList.add("is-send");
      button.dataset.delight = "send-comment";
      button.innerHTML = sendIcon;
      button.setAttribute("aria-label", "发送");
      button.setAttribute("title", "发送");
      scheduleActivityRailHeightSync();
      requestAnimationFrame(() => $("#delightCommentInput")?.focus());
    }

    function closeDelightComposer() {
      const actions = document.querySelector(".delight-main-actions");
      const shell = actions?.closest(".delight-actions");
      const button = actions?.querySelector(".chat-action");
      if (!actions || !button) return;
      shell?.classList.remove("is-composing");
      actions.classList.remove("is-composing");
      button.classList.remove("is-send");
      button.dataset.delight = "chat";
      button.textContent = "聊一聊";
      button.removeAttribute("aria-label");
      button.removeAttribute("title");
      scheduleActivityRailHeightSync();
    }

    // 用户是否正在首页内容上互动：惊喜聊天框展开 / 有焦点 / 有未发送草稿，
    // 或正在普通推荐卡上悬停、输入、提交可撤销反馈。后台推送与惊喜自动轮播
    // 在此期间不得切卡或重渲染——否则会让下方卡片在点击中途发生位移，或把
    // 惊喜聊天反馈记到换上来的新卡上。
    function delightUserEngaged() {
      const input = document.getElementById("delightCommentInput");
      const composing = Boolean(document.querySelector(".delight-main-actions.is-composing"));
      const focused = document.activeElement === input;
      const hasDraft = Boolean(String(input?.value || "").trim());
      const recommendationEngaged = Boolean(document.querySelector(
        "#videoGrid:hover, #videoGrid:focus-within, #videoGrid .is-feedback-pending, #videoGrid .card-actions.is-composing"
      ));
      return composing || focused || hasDraft || recommendationEngaged;
    }

    // 互动中新候选静默入队时只刷新右上角计数，不触碰卡片 DOM。
    function syncDelightCount() {
      if ($("#delightCount") && state.delights.length) {
        $("#delightCount").textContent = `${state.delightIndex + 1}/${state.delights.length}`;
      }
    }

    async function handleCardAction(action, item, card) {
      const status = card.querySelector(".status-line");
      if (card.dataset.feedbackPending === "true") return;
      if (action === "open") return openRecommendation(item, card);
      if (action === "comment") { openCardComposer(card); return; }
      if (action === "cancel-comment") { closeCardComposer(card); return; }
      if (recommendationFeedbackAlreadyCommitted(item, action)) {
        if (status) status.textContent = "这条推荐已经记录过喜欢，无需重复提交。";
        return;
      }
      if (action === "watch-later") {
        const btn = card.querySelector('[data-action="watch-later"]');
        const savedItem = desktopSavedItem(item);
        if (!btn || btn.disabled || desktopSavedMutations.isBusy("watch_later", savedItem.item_key)) return;
        btn.disabled = true;
        const wasSaved = desktopSavedMutations.isSaved("watch_later", savedItem.item_key);
        btn.setAttribute("aria-pressed", wasSaved ? "false" : "true");
        btn.title = wasSaved ? "稍后读" : "取消稍后读";
        btn.setAttribute("aria-label", btn.title);
        if (status) { status.removeAttribute("role"); status.textContent = wasSaved ? "正在从本地稍后读移除…" : "正在保存到本地稍后读…"; }
        try {
          await desktopSavedMutations.toggle("watch_later", savedItem.item_key, {
            add: () => saveDesktopItem("watch_later", savedItem),
            remove: () => removeDesktopSavedItem("watch_later", savedItem.item_key)
          });
          publishSavedChange("watch_later", savedItem.item_key, desktopSavedMutations.isSaved("watch_later", savedItem.item_key));
          if (status) status.textContent = wasSaved ? "已从本机稍后读移除。" : "已保存到本机稍后读。";
        } catch (error) {
          btn.setAttribute("aria-pressed", wasSaved ? "true" : "false");
          btn.title = wasSaved ? "\u53D6\u6D88\u7A0D\u540E\u518D\u770B" : "\u7A0D\u540E\u518D\u770B";
          if (status) { status.setAttribute("role", "alert"); status.textContent = error?.message || "本地保存失败，请重试。"; }
        } finally {
          btn.disabled = false;
          syncSavedControlsForRecommendation(item);
        }
        return;
      }
      if (action === "favorite") {
        const btn = card.querySelector('[data-action="favorite"]');
        const savedItem = desktopSavedItem(item);
        if (!btn || btn.disabled || desktopSavedMutations.isBusy("favorite", savedItem.item_key)) return;
        btn.disabled = true;
        const wasSaved = desktopSavedMutations.isSaved("favorite", savedItem.item_key);
        btn.setAttribute("aria-pressed", wasSaved ? "false" : "true");
        btn.title = wasSaved ? "\u6536\u85CF" : "\u53D6\u6D88\u6536\u85CF";
        btn.setAttribute("aria-label", btn.title);
        if (status) { status.removeAttribute("role"); status.textContent = wasSaved ? "正在从本地收藏移除…" : "正在保存到本地收藏…"; }
        try {
          await desktopSavedMutations.toggle("favorite", savedItem.item_key, {
            add: () => saveDesktopItem("favorite", savedItem),
            remove: () => removeDesktopSavedItem("favorite", savedItem.item_key)
          });
          publishSavedChange("favorite", savedItem.item_key, desktopSavedMutations.isSaved("favorite", savedItem.item_key));
          if (status) status.textContent = wasSaved ? "已从本机收藏移除。" : "已保存到本机收藏。";
        } catch (error) {
          btn.setAttribute("aria-pressed", wasSaved ? "true" : "false");
          btn.title = wasSaved ? "\u53D6\u6D88\u6536\u85CF" : "\u6536\u85CF";
          if (status) { status.setAttribute("role", "alert"); status.textContent = error?.message || "本地保存失败，请重试。"; }
        } finally {
          btn.disabled = false;
          syncSavedControlsForRecommendation(item);
        }
        return;
      }
      if (action === "send-comment") {
        const input = card.querySelector(".comment-field input");
        const note = input.value.trim();
        if (!note) {
          status.textContent = "先写一句反馈，再提交。";
          input?.focus();
          return;
        }
        const previousFeedbackType = item.feedback_type;
        if (input) input.value = "";
        closeCardComposer(card);
        item.feedback_type = "comment";
        status.textContent = "已提交推荐反馈，推荐会继续保留在当前列表。";
        finishRecommendationFeedback(card, "comment");
        showToast("已提交推荐反馈");
        void submitFeedback(item, "comment", note).catch((error) => {
          item.feedback_type = previousFeedbackType;
          if (input) input.value = note;
          status.textContent = configErrorMessage(error?.details) || error?.message || "反馈提交失败，请稍后重试。";
          showToast(status.textContent);
        });
        return;
      }
      const feedbackType = action === "like" ? "like" : action === "dismiss" ? "dismiss" : "dislike";
      stageRecommendationFeedback(item, card, feedbackType);
    }

    function renderRail() {
      const activityItems = state.activityItems.length ? state.activityItems : asArray(state.activity?.items);
      const activityMarkup = (limit) => activityItems.length
        ? activityItems.slice(0, limit).map((item) => `<div class="activity-item"><p>${escapeHtml(typeof item === "object" ? item.summary || item.detail || item.kind || valueList(item) : item)}</p></div>`).join("")
        : `<div class="empty-state">还没有新的动态；实时流收到 activity.added 后会自动刷新。</div>`;
      if ($("#activityList")) $("#activityList").innerHTML = activityMarkup(1);
      if ($("#mobileActivityList")) $("#mobileActivityList").innerHTML = activityMarkup(5);
      const mobileCount = $("#mobileMessageCount");
      if (mobileCount) mobileCount.textContent = String(getRenderableMessages().length);
    }

    function renderActivityHistory() {
      const list = $("#activityHistory");
      if (!list) return;
      if (!state.activityItems.length) {
        list.innerHTML = `<div class="empty-state">暂无历史动态。</div>`;
      } else {
        list.innerHTML = state.activityItems.map((item) => `<article class="activity-item"><p class="eyebrow">${escapeHtml(item.kind || "activity")}</p><h3>${escapeHtml(item.summary || "后台动态")}</h3><p class="video-meta">${escapeHtml(item.detail || item.created_at || "")}</p></article>`).join("");
      }
      const more = $("#activityMoreBtn");
      if (more) more.disabled = !state.activityHasMore;
    }

    async function loadActivityPage({ reset = false } = {}) {
      const cursor = reset ? "" : state.activityCursor;
      const query = new URLSearchParams({ limit: "10" });
      if (cursor) query.set("before", cursor);
      const payload = await requestJson(`${ENDPOINTS.activityFeed}?${query.toString()}`);
      if (!payload) { showToast("动态加载失败：后端不可用"); return; }
      const items = Array.isArray(payload.items) ? payload.items : [];
      state.activity = payload;
      state.activityItems = reset ? items : state.activityItems.concat(items);
      state.activityCursor = payload.next_cursor || payload.next || "";
      state.activityHasMore = Boolean(payload.has_more && state.activityCursor);
      renderRail();
      renderActivityHistory();
    }

    function formatPercent(value) {
      if (value == null || value === "") return "";
      if (typeof value === "string" && value.trim().endsWith("%")) return value.trim();
      const number = Number(value);
      if (!Number.isFinite(number)) return String(value);
      const normalized = Math.abs(number) <= 1 ? number * 100 : number;
      return `${Math.round(normalized)}%`;
    }

    function score01(value, fallback = 0.5) {
      const number = Number(value);
      if (!Number.isFinite(number)) return fallback;
      return Math.max(0, Math.min(1, Math.abs(number) <= 1 ? number : number / 100));
    }

    function formatPersonality(value) {
      if (!value) return "";
      if (typeof value !== "object") return String(value);
      const type = value.type || value.mbti || value.name || value.label;
      const confidence = formatPercent(value.confidence);
      if (type && confidence) return `${type}（置信度 ${confidence}）`;
      if (type) return String(type);
      return valueList(value);
    }

    function formatPersonalityType(value) {
      if (!value) return "";
      if (typeof value !== "object") return String(value);
      return String(value.type || value.mbti || value.name || value.label || "");
    }

    function formatProfileObject(value) {
      const preferred = value.domain || value.summary || value.name || value.title || value.label || value.value || value.text || value.reason || value.hypothesis || value.observation;
      if (preferred) return String(preferred);
      return Object.entries(value)
        .filter(([, val]) => val != null && val !== "")
        .map(([key, val]) => {
          if (key === "confidence") return `置信度 ${formatPercent(val)}`;
          if (key === "dimensions" && typeof val === "object") return "维度已在 MBTI 图表中展示";
          return `${key}: ${valueList(val)}`;
        })
        .filter(Boolean)
        .join(" / ");
    }

    function valueList(value) {
      if (value == null || value === "") return "";
      if (Array.isArray(value)) return value.map((item) => valueList(item)).filter(Boolean).join("、");
      if (typeof value === "object") return formatProfileObject(value);
      return String(value);
    }

    function asArray(value) {
      if (value == null || value === "") return [];
      if (Array.isArray(value)) return value;
      if (typeof value === "object") {
        if (Array.isArray(value.items)) return value.items;
        if (Array.isArray(value.domains)) return value.domains;
        if (Array.isArray(value.values)) return value.values;
        return Object.entries(value).map(([key, val]) => {
          if (val == null || val === "" || val === false) return "";
          if (val === true) return key;
          if (typeof val === "object" && !Array.isArray(val)) return { name: key, ...val };
          return `${key}: ${valueList(val)}`;
        }).filter(Boolean);
      }
      return String(value).split(/[、,\n]+/).map((item) => item.trim()).filter(Boolean);
    }

    function firstValue(...values) {
      return values.find((value) => value != null && value !== "" && (!Array.isArray(value) || value.length));
    }

    function chipsHtml(value, fallback = "这部分还在慢慢补。") {
      const items = Array.isArray(value) ? value.map(valueList).filter(Boolean) : valueList(value).split("、").filter(Boolean);
      if (!items.length) return `<p class="video-meta">${escapeHtml(fallback)}</p>`;
      return `<div class="profile-chip-list">${items.map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("")}</div>`;
    }

    function paragraphsHtml(value, fallback = "这部分还在观察，先不急着下结论。") {
      const text = valueList(value);
      if (!text) return `<p class="video-meta">${escapeHtml(fallback)}</p>`;
      return `<div class="profile-portrait-copy">${String(text).split(/\n+/).map((line) => line.trim()).filter(Boolean).map((line) => `<p class="video-meta">${escapeHtml(line)}</p>`).join("")}</div>`;
    }

    function profileItem(title, html, extraClass = "") {
      return `<article class="profile-item ${extraClass}"><h3>${escapeHtml(title)}</h3>${html}</article>`;
    }

    function profileLayer(label, items) {
      const body = items.filter(Boolean).join("");
      if (!body) return "";
      return `<div class="profile-layer"><div class="profile-layer-label">${escapeHtml(label)}</div>${body}</div>`;
    }

    function dimensionData(mbti, key) {
      if (!mbti?.dimensions) return null;
      return mbti.dimensions[key] || mbti.dimensions[`${key[0]}_${key[1]}`] || mbti.dimensions[key.toLowerCase()] || mbti.dimensions[`${key[0].toLowerCase()}_${key[1].toLowerCase()}`];
    }

    function normalizedPole(rawPole, key) {
      const pole = String(rawPole || "").trim().toUpperCase();
      if (pole.includes(key[0])) return key[0];
      if (pole.includes(key[1])) return key[1];
      return "";
    }

    function mbtiAxisHtml(mbti, config) {
      const dim = dimensionData(mbti, config.key);
      if (!dim) return "";
      const pole = normalizedPole(dim.pole, config.key) || config.key[1];
      const strength = score01(dim.strength, 0.5);
      const marker = pole === config.key[0] ? 50 - strength * 50 : 50 + strength * 50;
      const start = Math.min(50, marker);
      const width = Math.abs(marker - 50);
      return `<div class="mbti-axis">
        <span class="mbti-axis-side${pole === config.key[0] ? " is-active" : ""}">${config.left}<span> ${config.leftName}</span></span>
        <div class="mbti-axis-track" style="--start:${start}%;--width:${width}%;--marker:${marker}%"><span class="mbti-axis-fill"></span><span class="mbti-axis-marker"></span></div>
        <span class="mbti-axis-side${pole === config.key[1] ? " is-active" : ""}">${config.right}<span> ${config.rightName}</span></span>
        <span class="mbti-axis-pct">${escapeHtml(pole)} ${Math.round(strength * 100)}%</span>
      </div>`;
    }

    function mbtiHtml(value) {
      if (!value) return `<p class="video-meta">MBTI 还没推断出来，再多看一阵。</p>`;
      if (typeof value !== "object") return `<p class="video-meta">${escapeHtml(value)}</p>`;
      const type = value.type || value.mbti || value.name || "—";
      const axes = [
        { key: "EI", left: "E", right: "I", leftName: "外向", rightName: "内向" },
        { key: "SN", left: "S", right: "N", leftName: "实感", rightName: "直觉" },
        { key: "TF", left: "T", right: "F", leftName: "思考", rightName: "情感" },
        { key: "JP", left: "J", right: "P", leftName: "判断", rightName: "知觉" }
      ].map((config) => mbtiAxisHtml(value, config)).filter(Boolean).join("");
      return `<div class="mbti-block"><div class="mbti-type-row"><span class="mbti-type-label">${escapeHtml(type)}</span>${value.confidence ? `<span class="mbti-confidence">整体可信度 ${formatPercent(value.confidence)}</span>` : ""}</div>${axes ? `<div class="mbti-dimensions">${axes}</div>` : ""}</div>`;
    }

    function interestTreeHtml(value, fallback) {
      const domains = asArray(value);
      if (!domains.length) return `<p class="video-meta">${escapeHtml(fallback)}</p>`;
      return `<div class="profile-interest-tree">${domains.map((item) => {
        if (typeof item !== "object") return `<div class="profile-domain"><div class="profile-domain-head"><span class="profile-domain-title">${escapeHtml(item)}</span></div></div>`;
        const title = item.domain || item.name || item.title || valueList(item);
        const weight = item.weight != null ? `<span class="profile-domain-weight">${formatPercent(item.weight)}</span>` : "";
        const specifics = asArray(item.specifics).map((s) => s?.name || s?.label || valueList(s)).filter(Boolean);
        return `<div class="profile-domain"><div class="profile-domain-head"><span class="profile-domain-title">${escapeHtml(title)}</span>${weight}</div>${specifics.length ? `<div class="profile-chip-list">${specifics.map((s) => `<span class="chip">${escapeHtml(s)}</span>`).join("")}</div>` : ""}</div>`;
      }).join("")}</div>`;
    }

    function meterHtml(label, value) {
      const score = score01(value);
      return `<div class="profile-meter"><div class="profile-meter-head"><span>${escapeHtml(label)}</span><strong>${Math.round(score * 100)}%</strong></div><div class="profile-meter-track"><div class="profile-meter-fill" style="width:${score * 100}%"></div></div></div>`;
    }

    function isKnownText(value) {
      const text = String(value == null ? "" : value).trim().toLowerCase();
      return text !== "" && !["unknown", "none", "n/a", "未知"].includes(text);
    }

    function styleHtml(style) {
      if (!style || typeof style !== "object" || Array.isArray(style)) return paragraphsHtml(style, "内容口味还在继续归拢。");
      const textRows = [
        ["偏好时长", style.preferred_duration],
        ["偏好节奏", style.preferred_pace]
      ].filter(([, value]) => isKnownText(value)).map(([label, value]) => `<div class="profile-context-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
      const bars = [
        ["质量敏感度", style.quality_sensitivity],
        ["幽默偏好", style.humor_preference],
        ["深度偏好", style.depth_preference]
      ].filter(([, value]) => value != null).map(([label, value]) => meterHtml(label, value)).join("");
      return `<div class="profile-bars profile-style-bars">${textRows}${bars}</div>`;
    }

    function contextHtml(context) {
      if (!context || typeof context !== "object" || Array.isArray(context)) return paragraphsHtml(context, "使用场景还在继续观察。");
      const rows = [
        ["工作日", context.weekday_patterns],
        ["周末", context.weekend_patterns],
        ["一天中的时段", context.time_of_day_patterns],
        ["资讯使用场景", context.session_type]
      ].filter(([, value]) => isKnownText(value)).map(([label, value]) => `<div class="profile-context-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
      return rows ? `<div class="profile-context">${rows}</div>` : paragraphsHtml("", "使用场景还在继续观察。");
    }

    function speculativeHtml(items, options = {}) {
      const isAvoidance = options.kind === "avoidance";
      const probeType = isAvoidance ? "avoidance.probe" : "interest.probe";
      const actionCopy = probeActionCopy(probeType);
      const list = asArray(items).filter((item) => {
        if (typeof item !== "object") return !state.handledProbeKeys.has(probeKey(probeType, item));
        const domain = item.domain || item.name || item.title;
        if (!domain || state.handledProbeKeys.has(probeKey(probeType, domain))) return false;
        const status = String(item.status || "active").trim().toLowerCase();
        return status === "active" || status === "pending";
      });
      if (!list.length) return `<p class="video-meta">${isAvoidance ? "新闻爪暂时没有待确认的避雷方向。" : "新闻爪还没有正在试探的新方向。"}</p>`;
      const statusLabels = { active: "待确认", pending: "待观察", confirmed: "已确认", deprecated: "已弃", rejected: "已排除" };
      const fallbackTitle = isAvoidance ? "猜测避雷" : "猜测兴趣";
      return `<div class="speculative-list">${list.map((item) => {
        if (typeof item !== "object") return `<div class="speculative-item"><div class="spec-header"><span class="spec-domain">${escapeHtml(item)}</span></div></div>`;
        const domain = item.domain || item.name || item.title || fallbackTitle;
        const status = item.status || "active";
        const count = Number(item.confirmation_count ?? 0);
        const threshold = Number(item.confirmation_threshold ?? 3);
        const progress = `${count}/${threshold} 次确认`;
        const confidence = score01(item.confidence, 0);
        const specifics = asArray(item.specifics).map((s) => ({
          name: s?.name || s?.label || valueList(s),
          count: Number(s?.confirmation_count ?? 0)
        })).filter((s) => s.name);
        return `<div class="speculative-item is-status-${escapeHtml(status)}" data-spec-domain="${escapeHtml(domain)}">
          <div class="spec-header">
            <span class="spec-domain">${escapeHtml(domain)}</span>
            ${statusLabels[status] ? `<span class="spec-status">${escapeHtml(statusLabels[status])}</span>` : ""}
            <span class="spec-progress">${escapeHtml(progress)}</span>
          </div>
          ${confidence > 0 ? `<div class="spec-confidence-row"><div class="spec-confidence-bar"><div class="spec-confidence-fill" style="width:${Math.round(confidence * 100)}%"></div></div><span class="spec-confidence-label">置信度 ${Math.round(confidence * 100)}%</span></div>` : ""}
          ${item.reason ? `<p class="video-meta">${escapeHtml(item.reason)}</p>` : ""}
          ${specifics.length ? `<div class="spec-specifics">${specifics.map((s) => `<span class="spec-specific-chip">${escapeHtml(s.name)}${s.count > 0 ? `<span class="spec-specific-count">${s.count}</span>` : ""}</span>`).join("")}</div>` : ""}
          <p class="spec-help">${isAvoidance ? `置信度表示新闻爪认为你会避开这个方向的把握；确认次数来自后端累计的避雷确认信号，达到 ${threshold} 次后会进入更稳定的避雷画像。` : `置信度表示新闻爪认为你会喜欢这个方向的把握；确认次数来自后端累计的正向确认信号（包括但不限于这里的“喜欢”），达到 ${threshold} 次后会进入更稳定的兴趣画像。`}</p>
          ${status === "active" && domain ? `<div class="spec-actions"><button class="probe-btn is-confirm" type="button" data-spec-response="confirm" data-spec-type="${probeType}">${actionCopy.confirm}</button><button class="probe-btn is-neutral" type="button" data-spec-response="defer" data-spec-type="${probeType}">${actionCopy.defer}</button><button class="probe-btn is-reject" type="button" data-spec-response="reject" data-spec-type="${probeType}">${actionCopy.reject}</button></div>` : ""}
        </div>`;
      }).join("")}</div>`;
    }

    function memoryHtml(items) {
      const list = asArray(items);
      if (!list.length) return `<p class="video-meta">新闻爪还在继续观察，过一阵这里会更具体。</p>`;
      return `<div class="profile-card-list">${list.slice(0, 8).map((item) => {
        if (typeof item !== "object") return `<div class="profile-memory"><p class="video-meta">${escapeHtml(item)}</p></div>`;
        const meta = item.sourceLabel || item.source_label || item.source || item.created_at || "";
        const details = asArray([item.contextLine || item.context_line, item.impact, item.reasoning, item.evidence]).filter(Boolean).map((line) => `<p class="video-meta">${escapeHtml(valueList(line))}</p>`).join("");
        return `<div class="profile-memory"><div class="profile-memory-head"><strong>${escapeHtml(item.summary || item.title || "近期记忆")}</strong>${meta ? `<span class="profile-memory-meta">${escapeHtml(meta)}</span>` : ""}</div>${details}</div>`;
      }).join("")}</div>`;
    }

    function insightsHtml(items) {
      const list = asArray(items);
      if (!list.length) return `<p class="video-meta">当前没有需要特别展示的活跃洞察。</p>`;
      return `<div class="profile-card-list">${list.map((item) => {
        if (typeof item !== "object") return `<div class="profile-insight"><div class="profile-insight-head"><span class="profile-insight-title">${escapeHtml(item)}</span></div></div>`;
        const evidence = asArray(item.evidence).join("、");
        const hypothesis = item.hypothesis || "";
        return `<div class="profile-insight"><div class="profile-insight-head"><span class="profile-insight-title">${escapeHtml(hypothesis || item.observation || valueList(item))}</span><span class="profile-confidence">${formatPercent(item.confidence)}</span></div>${evidence ? `<p class="video-meta">证据：${escapeHtml(evidence)}</p>` : ""}${item.validated ? `<p class="video-meta">已验证</p>` : ""}</div>`;
      }).join("")}</div><p class="video-meta insight-readonly-hint">洞察区只读；请在对话的待聊确认入口继续。</p>`;
    }

    function awarenessHtml(items) {
      const list = asArray(items);
      if (!list.length) return `<p class="video-meta">近期观察还在沉淀。</p>`;
      return `<div class="profile-card-list">${list.map((item) => typeof item === "object" ? `<div class="profile-insight"><div class="profile-insight-head"><span class="profile-insight-title">${escapeHtml(item.observation || valueList(item))}</span>${item.date ? `<span class="profile-confidence">${escapeHtml(item.date)}</span>` : ""}</div>${item.trend ? `<p class="video-meta">趋势：${escapeHtml(item.trend)}</p>` : ""}${item.emotion_guess ? `<p class="video-meta">情绪猜测：${escapeHtml(item.emotion_guess)}</p>` : ""}</div>` : `<div class="profile-insight"><div class="profile-insight-head"><span class="profile-insight-title">${escapeHtml(item)}</span></div></div>`).join("")}</div>`;
    }

    function updateProfileMemoryButton() {
      const button = $("#profileMemoryMoreBtn");
      if (!button) return;
      button.hidden = !state.profileCognitionHasMore;
      button.disabled = !state.profileCognitionHasMore;
    }

    function syncProfileCognitionState(profile) {
      const cursor = profile?.next_cognition_cursor || profile?.next_cursor || "";
      state.profileCognitionCursor = cursor;
      state.profileCognitionHasMore = Boolean(profile?.has_more_cognition_updates && cursor);
      updateProfileMemoryButton();
    }

    function renderProfileDetails() {
      const legacyMount = document.getElementById("legacyProfileMount");
      if (state.newsclawMode !== false || !legacyMount) return;
      const profile = state.profile;
      if (!profile) {
        $("#profileDetails").innerHTML = profileItem("画像还没攒起来", paragraphsHtml("后端未连接或画像尚未初始化。连接 FastAPI 后会展示完整画像。"));
        state.profileCognitionHasMore = false;
        updateProfileMemoryButton();
        return;
      }
      if (state.editingProfile) {
        $("#profileDetails").innerHTML = renderProfileEditPanel();
        bindProfileEditActions();
        state.profileCognitionHasMore = false;
        updateProfileMemoryButton();
        return;
      }
      syncProfileCognitionState(profile);
      const html = [
        profileItem("这会儿的你", paragraphsHtml(profile.personality_portrait || profile.summary), "profile-portrait-block"),
        profileLayer("Core — 比较稳定的底色", [
          profileItem("核心特质", chipsHtml(profile.core_traits, "这部分还在慢慢补。")),
          profileItem("深层需求", chipsHtml(profile.deep_needs, "这块还要再多看一点。")),
          profileItem("MBTI / 人格推断", mbtiHtml(firstValue(profile.mbti, profile.personality_type)))
        ]),
        profileLayer("Values — 你在内容里长期在找什么", [
          profileItem("价值偏好", chipsHtml(firstValue(profile.values, profile.value_preferences), "价值偏好还在继续归拢。")),
          profileItem("内在驱动力", chipsHtml(firstValue(profile.motivational_drivers, profile.intrinsic_drives, profile.motivations), "这块还要再多看一点。"))
        ]),
        profileLayer("Interest — 你最近在看什么", [
          profileItem("感兴趣的方向", interestTreeHtml(profile.likes, "再刷一阵，这里会更准。")),
          profileItem("明显会避开", interestTreeHtml(profile.dislikes, "这块还在继续确认，先别急着下死结论。")),
          profileItem("常看的 媒体 / 记者", chipsHtml(firstValue(profile.favorite_up_users, profile.favorite_creators, profile.creators, profile.up_names), "常看的 媒体 / 记者还在统计。"))
        ]),
        profileLayer("Role — 这阵子的状态", [
          profileItem("大致处在什么阶段", paragraphsHtml(profile.life_stage, "这块还在观察，先不急着定论。")),
          profileItem("这阵子更像在经历什么", paragraphsHtml(firstValue(profile.current_phase, profile.current_stage), "这阵子的变化还在继续看。"))
        ]),
        profileLayer("Surface — 你怎么看内容", [
          profileItem("认知风格", chipsHtml(profile.cognitive_style, "这层还在继续归拢。")),
          profileItem("内容口味", styleHtml(firstValue(profile.style, profile.content_style, profile.content_preferences))),
          profileItem("使用场景", contextHtml(firstValue(profile.context, profile.current_context))),
          profileItem("探索开放度", meterHtml("愿意走出既有兴趣圈", firstValue(profile.exploration_openness, profile.openness)))
        ]),
        profileLayer("Speculate — 新闻爪在试探的方向", [
          profileItem("猜测兴趣", speculativeHtml(profile.speculative_interests)),
          profileItem("猜测避雷", speculativeHtml(profile.speculative_avoidances, { kind: "avoidance" })),
          profileItem("新闻爪最近新记住了什么", memoryHtml(firstValue(profile.recent_cognition_updates, profile.recent_memories)))
        ]),
        profileLayer("Signals — 正在推断中", [
          profileItem("当前活跃的洞察", insightsHtml(profile.active_insights)),
          profileItem("近期观察到的", awarenessHtml(profile.recent_awareness))
        ])
      ].join("");
      const profileEditBar = `<div class="profile-edit-bar"><button class="pill-btn" type="button" data-profile-edit-toggle="enter">✏️ 编辑画像</button></div>`;
      $("#profileDetails").innerHTML = profileEditBar + html;
      bindSpeculativeActions();
      bindProfileEditToggle();
    }

    // ── Editable profile (Phase 3, desktop) ──────────────────────
    const PROFILE_EDIT_LABELS = {
      personality_portrait: "人格素描",
      "core.core_traits": "核心特质",
      "core.deep_needs": "深层需求",
      "values_layer.values": "价值偏好",
      "values_layer.motivational_drivers": "内在驱动力",
      likes: "感兴趣的方向",
      dislikes: "明显会避开",
      "interest.favorite_up_users": "常看的 媒体 / 记者",
      "role.life_stage": "大致处在什么阶段",
      "role.current_phase": "这阵子更像在经历什么",
      "surface.cognitive_style": "认知风格",
      "surface.exploration_openness": "探索开放度",
      "surface.style.quality_sensitivity": "质量敏感度",
      "surface.style.humor_preference": "幽默偏好",
      "surface.style.depth_preference": "深度偏好"
    };
    const PROFILE_EDIT_ORDER = [
      "personality_portrait",
      "core.core_traits",
      "core.deep_needs",
      "values_layer.values",
      "values_layer.motivational_drivers",
      "likes",
      "dislikes",
      "interest.favorite_up_users",
      "role.life_stage",
      "role.current_phase",
      "surface.cognitive_style",
      "surface.exploration_openness",
      "surface.style.quality_sensitivity",
      "surface.style.humor_preference",
      "surface.style.depth_preference"
    ];

    function bindProfileEditToggle() {
      const btn = document.querySelector('#profileDetails [data-profile-edit-toggle="enter"]');
      if (btn) btn.addEventListener("click", () => { void enterProfileEdit(); });
    }

    async function enterProfileEdit() {
      state.editingProfile = true;
      state.profileEditState = null;
      renderProfileDetails();
      state.profileEditState = await requestJson(ENDPOINTS.profileEditState);
      renderProfileDetails();
    }

    async function exitProfileEdit() {
      state.editingProfile = false;
      state.profileEditState = null;
      const fresh = await requestJson(ENDPOINTS.profile);
      if (fresh) state.profile = fresh;
      renderProfileDetails();
    }

    async function applyProfileEdit(payload) {
      const res = await requestJson(ENDPOINTS.profileEdit, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (res && res.edit_state && res.edit_state.initialized) {
        state.profileEditState = res.edit_state;
      } else {
        const refreshed = await requestJson(ENDPOINTS.profileEditState);
        if (refreshed) state.profileEditState = refreshed;
        if (!res) showToast("修改未保存：请检查输入或后端状态");
      }
      renderProfileDetails();
    }

    function profileEditTextField(path, label, field) {
      const pinned = Boolean(field.pinned);
      const rows = path === "personality_portrait" ? 4 : 2;
      return `
        <div class="edit-field">
          <div class="edit-field-head"><span class="edit-field-label">${escapeHtml(label)}</span>${pinned ? `<span class="edit-badge">已编辑</span>` : ""}</div>
          <textarea class="edit-text-input" data-edit-text="${escapeHtml(path)}" rows="${rows}">${escapeHtml(field.value || "")}</textarea>
          ${field.ai_suggestion ? `<p class="edit-drift-hint">AI 当前想更新为：${escapeHtml(field.ai_suggestion)}</p>` : ""}
          <div class="edit-field-actions">
            <button class="pill-btn primary" type="button" data-edit-save="${escapeHtml(path)}">保存</button>
            ${pinned ? `<button class="edit-reset-btn" type="button" data-edit-reset="${escapeHtml(path)}">恢复 AI 建议</button>` : ""}
          </div>
        </div>`;
    }

    function profileEditScalarField(path, label, field) {
      const pinned = Boolean(field.pinned);
      const pct = Math.round((Number(field.value) || 0) * 100);
      const aiPct = typeof field.ai_suggestion === "number" ? Math.round(field.ai_suggestion * 100) : null;
      return `
        <div class="edit-field">
          <div class="edit-field-head"><span class="edit-field-label">${escapeHtml(label)}</span>${pinned ? `<span class="edit-badge">已编辑</span>` : ""}</div>
          <div class="edit-scalar-row">
            <input class="edit-scalar-input" type="range" min="0" max="100" step="1" value="${pct}" data-edit-scalar="${escapeHtml(path)}" />
            <span class="edit-scalar-value" data-edit-scalar-value="${escapeHtml(path)}">${pct}%</span>
          </div>
          ${aiPct !== null ? `<p class="edit-drift-hint">AI 当前想更新为：${aiPct}%</p>` : ""}
          <div class="edit-field-actions">
            <button class="pill-btn primary" type="button" data-edit-save-scalar="${escapeHtml(path)}">保存</button>
            ${pinned ? `<button class="edit-reset-btn" type="button" data-edit-reset="${escapeHtml(path)}">恢复 AI 建议</button>` : ""}
          </div>
        </div>`;
    }

    function profileEditListField(path, label, field) {
      const items = Array.isArray(field.items) ? field.items : [];
      const edited = (field.added?.length || 0) > 0 || (field.removed?.length || 0) > 0;
      const chips = items.length
        ? items.map((it) => `<span class="edit-chip">${escapeHtml(it)}<button class="edit-chip-remove" type="button" data-edit-remove="${escapeHtml(path)}" data-edit-value="${escapeHtml(it)}">✕</button></span>`).join("")
        : `<p class="video-meta">还没有，添加一个吧</p>`;
      return `
        <div class="edit-field">
          <div class="edit-field-head"><span class="edit-field-label">${escapeHtml(label)}</span>${edited ? `<span class="edit-badge">已编辑</span>` : ""}</div>
          <div class="edit-chip-list">${chips}</div>
          <div class="edit-add-row">
            <input class="edit-add-input" data-edit-add-input="${escapeHtml(path)}" placeholder="添加一项" />
            <button class="pill-btn" type="button" data-edit-add="${escapeHtml(path)}">添加</button>
          </div>
          ${edited ? `<div class="edit-field-actions"><button class="edit-reset-btn" type="button" data-edit-reset="${escapeHtml(path)}">恢复 AI 建议</button></div>` : ""}
        </div>`;
    }

    function profileEditSpecificName(item) {
      if (typeof item === "string") return item;
      if (item && typeof item === "object") return item.name || item.label || "";
      return "";
    }

    function profileEditHasSpecificEdits(field) {
      const edits = field?.specific_edits;
      if (!edits || typeof edits !== "object") return false;
      return Object.values(edits).some((edit) => {
        if (!edit || typeof edit !== "object") return false;
        return (edit.add?.length || 0) > 0 || (edit.remove?.length || 0) > 0;
      });
    }

    function profileEditInterestField(path, label, field) {
      const domains = Array.isArray(field.domains) ? field.domains : [];
      const edited = (field.removed_domains?.length || 0) > 0 || domains.some((d) => d?.user_added) || profileEditHasSpecificEdits(field);
      const tree = domains.length
        ? domains.map((d) => {
          if (!d?.domain) return "";
          const specifics = Array.isArray(d.specifics) ? d.specifics.map(profileEditSpecificName).filter(Boolean) : [];
          const specificChips = specifics.length
            ? specifics.map((specific) => `<span class="edit-chip edit-specific-chip">${escapeHtml(specific)}<button class="edit-chip-remove" type="button" data-edit-remove-specific="${escapeHtml(path)}" data-edit-parent="${escapeHtml(d.domain)}" data-edit-value="${escapeHtml(specific)}">✕</button></span>`).join("")
            : `<p class="video-meta edit-specific-empty">还没有二级兴趣</p>`;
          return `
            <div class="edit-interest-domain">
              <div class="edit-interest-domain-head">
                <span class="edit-chip edit-domain-chip">${escapeHtml(d.domain)}${d.user_added ? " ＋" : ""}<button class="edit-chip-remove" type="button" data-edit-remove="${escapeHtml(path)}" data-edit-value="${escapeHtml(d.domain)}">✕</button></span>
              </div>
              <div class="edit-specific-list">${specificChips}</div>
              <div class="edit-add-row edit-specific-add-row">
                <input class="edit-add-input" data-edit-specific-input="${escapeHtml(path)}" data-edit-parent="${escapeHtml(d.domain)}" placeholder="添加二级兴趣" />
                <button class="pill-btn" type="button" data-edit-add-specific="${escapeHtml(path)}" data-edit-parent="${escapeHtml(d.domain)}">添加</button>
              </div>
            </div>`;
        }).join("")
        : `<p class="video-meta">还没有，添加一个吧</p>`;
      const placeholder = path === "dislikes" ? "添加要避开的领域" : "添加感兴趣的领域";
      return `
        <div class="edit-field">
          <div class="edit-field-head"><span class="edit-field-label">${escapeHtml(label)}</span>${edited ? `<span class="edit-badge">已编辑</span>` : ""}</div>
          <div class="edit-interest-tree">${tree}</div>
          <div class="edit-add-row">
            <input class="edit-add-input" data-edit-add-input="${escapeHtml(path)}" placeholder="${escapeHtml(placeholder)}" />
            <button class="pill-btn" type="button" data-edit-add="${escapeHtml(path)}">添加</button>
          </div>
          ${edited ? `<div class="edit-field-actions"><button class="edit-reset-btn" type="button" data-edit-reset="${escapeHtml(path)}">恢复 AI 建议</button></div>` : ""}
        </div>`;
    }

    function renderProfileEditPanel() {
      const editState = state.profileEditState;
      let html = `<div class="profile-edit-bar"><button class="pill-btn" type="button" data-profile-edit-toggle="exit">✓ 完成</button></div>`;
      if (!editState) {
        html += `<p class="video-meta">加载中…</p>`;
        return html;
      }
      if (!editState.initialized || !editState.fields) {
        html += `<p class="video-meta">画像还没攒起来，回到首页推荐区点「开始初始化」后再回来编辑。</p>`;
        return html;
      }
      html += `<p class="video-meta profile-edit-note">标签 / 兴趣类增删即时生效；文本与滑杆类改完点「保存」才生效。改动都不会被后续自动重建覆盖，删错了点「恢复 AI 建议」即可。</p>`;
      for (const path of PROFILE_EDIT_ORDER) {
        const field = editState.fields[path];
        if (!field || typeof field !== "object") continue;
        const label = PROFILE_EDIT_LABELS[path] || path;
        if (field.type === "text") html += profileEditTextField(path, label, field);
        else if (field.type === "scalar") html += profileEditScalarField(path, label, field);
        else if (field.type === "list") html += profileEditListField(path, label, field);
        else if (field.type === "interest") html += profileEditInterestField(path, label, field);
      }
      return html;
    }

    function bindProfileEditActions() {
      const root = $("#profileDetails");
      if (!root) return;
      root.querySelector('[data-profile-edit-toggle="exit"]')?.addEventListener("click", () => { void exitProfileEdit(); });
      root.querySelectorAll("[data-edit-remove]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          if (btn.disabled) return;
          const chip = btn.closest(".edit-chip");
          if (chip?.classList.contains("is-pending")) return;
          chip?.classList.add("is-pending");
          btn.disabled = true;
          await applyProfileEdit({ target: btn.dataset.editRemove, op: "remove", value: btn.dataset.editValue });
        });
      });
      root.querySelectorAll("[data-edit-remove-specific]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          if (btn.disabled) return;
          const chip = btn.closest(".edit-chip");
          if (chip?.classList.contains("is-pending")) return;
          chip?.classList.add("is-pending");
          btn.disabled = true;
          await applyProfileEdit({
            target: btn.dataset.editRemoveSpecific,
            op: "remove",
            value: btn.dataset.editValue,
            parent: btn.dataset.editParent || ""
          });
        });
      });
      root.querySelectorAll("[data-edit-reset]").forEach((btn) => {
        btn.addEventListener("click", () => void applyProfileEdit({ target: btn.dataset.editReset, op: "reset" }));
      });
      root.querySelectorAll("[data-edit-add]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          if (btn.disabled) return;
          const path = btn.dataset.editAdd;
          const input = root.querySelector(`[data-edit-add-input="${path}"]`);
          const value = input?.value.trim();
          if (!value) return;
          btn.disabled = true;
          await applyProfileEdit({ target: path, op: "add", value });
        });
      });
      root.querySelectorAll("[data-edit-add-specific]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          if (btn.disabled) return;
          const input = btn.closest(".edit-add-row")?.querySelector("[data-edit-specific-input]");
          const value = input?.value.trim();
          if (!value) return;
          btn.disabled = true;
          await applyProfileEdit({
            target: btn.dataset.editAddSpecific,
            op: "add",
            value,
            parent: btn.dataset.editParent || ""
          });
        });
      });
      root.querySelectorAll("[data-edit-add-input]").forEach((input) => {
        input.addEventListener("keydown", async (event) => {
          if (event.key !== "Enter") return;
          event.preventDefault();
          const value = input.value.trim();
          if (!value) return;
          const button = root.querySelector(`[data-edit-add="${input.dataset.editAddInput}"]`);
          if (button?.disabled) return;
          if (button) button.disabled = true;
          await applyProfileEdit({ target: input.dataset.editAddInput, op: "add", value });
        });
      });
      root.querySelectorAll("[data-edit-specific-input]").forEach((input) => {
        input.addEventListener("keydown", async (event) => {
          if (event.key !== "Enter") return;
          event.preventDefault();
          const value = input.value.trim();
          if (!value) return;
          const button = input.closest(".edit-add-row")?.querySelector("[data-edit-add-specific]");
          if (button?.disabled) return;
          if (button) button.disabled = true;
          await applyProfileEdit({
            target: input.dataset.editSpecificInput,
            op: "add",
            value,
            parent: input.dataset.editParent || ""
          });
        });
      });
      root.querySelectorAll("[data-edit-save]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const path = btn.dataset.editSave;
          const textarea = root.querySelector(`[data-edit-text="${path}"]`);
          const value = textarea?.value.trim();
          if (!value) return;
          void applyProfileEdit({ target: path, op: "set", value });
        });
      });
      root.querySelectorAll("[data-edit-scalar]").forEach((input) => {
        input.addEventListener("input", () => {
          const out = root.querySelector(`[data-edit-scalar-value="${input.dataset.editScalar}"]`);
          if (out) out.textContent = `${input.value}%`;
        });
      });
      root.querySelectorAll("[data-edit-save-scalar]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const path = btn.dataset.editSaveScalar;
          const input = root.querySelector(`[data-edit-scalar="${path}"]`);
          if (!input) return;
          void applyProfileEdit({ target: path, op: "set", value: Number(input.value) / 100 });
        });
      });
    }

    async function loadMoreProfileMemory() {
      if (!state.profileCognitionCursor) return;
      const button = $("#profileMemoryMoreBtn");
      if (button) button.disabled = true;
      const query = new URLSearchParams({ cursor: state.profileCognitionCursor });
      const nextPage = await requestJson(`${ENDPOINTS.profile}?${query.toString()}`);
      if (!nextPage) {
        showToast("近期记忆加载失败：后端不可用");
        updateProfileMemoryButton();
        return;
      }
      const current = Array.isArray(state.profile?.recent_cognition_updates) ? state.profile.recent_cognition_updates : [];
      const incoming = Array.isArray(nextPage.recent_cognition_updates) ? nextPage.recent_cognition_updates : [];
      state.profile = {
        ...(state.profile || {}),
        ...nextPage,
        recent_cognition_updates: current.concat(incoming)
      };
      syncProfileCognitionState(state.profile);
      renderProfileDetails();
      showToast(incoming.length ? `已加载 ${incoming.length} 条近期记忆` : "没有更多近期记忆");
    }

    function messageType(msg) {
      const type = msg?.type === "probe" ? "interest.probe" : (msg?.type || "interest.probe");
      return type === "avoidance" ? "avoidance.probe" : type;
    }

    function isAvoidanceProbe(type) {
      return messageType({ type }) === "avoidance.probe";
    }

    const PROBE_ACTION_COPY = Object.freeze({
      interest: Object.freeze({
        confirm: "确认喜欢",
        defer: "暂时搁置",
        reject: "确认不喜欢",
        chat: "多聊聊",
      }),
      avoidance: Object.freeze({
        confirm: "确认避雷",
        defer: "搁置避雷",
        reject: "不是雷点",
        chat: "多聊聊",
      }),
    });

    function probeActionCopy(type) {
      return PROBE_ACTION_COPY[isAvoidanceProbe(type) ? "avoidance" : "interest"];
    }

    function isChallengeProbe(item) {
      const mode = String(item?.probe_mode || "").toLowerCase();
      return Boolean(item?.challenge) || mode === "lateral" || mode === "bridge" || mode === "wildcard";
    }

    function probeKey(type, domain) {
      const normalizedDomain = String(domain || "").trim().toLowerCase();
      return normalizedDomain ? `${messageType({ type })}:${normalizedDomain}` : "";
    }

    function messageKey(msg) {
      const type = messageType(msg);
      if (type === "web-monitor-notification") {
        return `${type}:${msg?.delivery_id || ""}`;
      }
      if (type === "interest.probe" || type === "avoidance.probe") {
        return probeKey(type, msg?.domain || msg?.title);
      }
      return `${type}:${msg?.bvid || msg?.domain || msg?.title || msg?.reason || ""}`;
    }

    function normalizeMessageItem(item) {
      if (!item) return null;
      const type = messageType(item);
      if (type === "delight") {
        return null;
      }
      if (type === "notification") {
        const bvid = item.bvid || item.id || item.recommendation_id;
        if (!bvid) return null;
        return {
          type: "notification",
          bvid: String(bvid),
          title: item.title || "有一条值得通知你的推荐",
          reason: item.reason || item.expression || "这条推荐达到了通知阈值。",
          content_url: item.content_url || item.canonical_url || ""
        };
      }
      if (type === "web-monitor-notification") {
        if (!item.delivery_id) return null;
        return {
          type,
          delivery_id: String(item.delivery_id),
          monitor_id: String(item.monitor_id || ""),
          event_id: String(item.event_id || ""),
          change_id: String(item.change_id || ""),
          kind: String(item.kind || "change"),
          title: item.title || "监控网页有新变化",
          reason: item.body || "网页正文出现了可核验的变化。",
          created_at: item.created_at || "",
        };
      }
      const domain = item.domain || item.name || item.title;
      if (!domain) return null;
      const probeType = type === "avoidance.probe" || item.kind === "avoidance" ? "avoidance.probe" : "interest.probe";
      if (state.handledProbeKeys.has(probeKey(probeType, domain))) return null;
      const status = String(item.status || "active").trim().toLowerCase();
      if (status !== "active" && status !== "pending") return null;
      return {
        type: probeType,
        domain: String(domain),
        reason: item.reason || item.message || item.description || (probeType === "avoidance.probe" ? "后端希望确认这个避雷方向。" : "后端希望确认这个兴趣方向。"),
        specifics: asArray(item.specifics || item.examples || item.children).map((s) => s?.name || s?.label || valueList(s)).filter(Boolean),
        probe_mode: item.probe_mode || "",
        challenge: Boolean(item.challenge),
        chat_status: item.chat_status || item.status_text || "",
        chat_reply: item.chat_reply || item.reply || ""
      };
    }

    function syncMessageCount() {
      const count = getRenderableMessages(state.messageListSnapshot && isMessagesDrawerOpen() ? state.messageListSnapshot : state.messages).length;
      if (state.runtimeStatus) state.runtimeStatus.unread_count = count;
      const metric = $("#metricUnread");
      if (metric) metric.textContent = String(count);
      const dot = $("#messagesDot");
      if (dot) dot.hidden = count <= 0;
      const mobileCount = $("#mobileMessageCount");
      if (mobileCount) mobileCount.textContent = String(count);
      return count;
    }

    function getRenderableMessages(source = state.messages) {
      const seen = new Set();
      const items = [];
      for (const raw of source || []) {
        const item = normalizeMessageItem(raw);
        if (!item) continue;
        const key = messageKey(item);
        if (!key || seen.has(key)) continue;
        seen.add(key);
        items.push(item);
      }
      return items;
    }

    function isMessagesDrawerOpen() {
      return Boolean($("#messagesDrawer")?.classList.contains("is-open"));
    }

    function hydrateInboxFromSpeculations(speculations, type = "interest.probe") {
      if (speculations == null || speculations === "") return;
      const normalizedType = messageType({ type });
      const items = asArray(speculations);
      const active = items.filter((item) => item && item.domain && (!item.status || item.status === "active") && !state.handledProbeKeys.has(probeKey(normalizedType, item.domain)));
      const activeKeys = new Set(active.map((item) => probeKey(normalizedType, item.domain)));
      const preserveCurrentProbeList = isMessagesDrawerOpen();
      state.messages = state.messages.filter((msg) => {
        if (messageType(msg) !== normalizedType) return true;
        const domain = String(msg.domain || "");
        if (!domain || state.handledProbeKeys.has(probeKey(normalizedType, domain))) return false;
        if (state.resolvingMessageKeys.has(messageKey(msg))) return true;
        return preserveCurrentProbeList || activeKeys.has(probeKey(normalizedType, domain));
      });
      const existing = new Set(state.messages.filter((msg) => messageType(msg) === normalizedType).map((msg) => probeKey(normalizedType, msg.domain)));
      for (const item of active) {
        const domain = String(item.domain);
        const key = probeKey(normalizedType, domain);
        if (!key || state.handledProbeKeys.has(key) || existing.has(key)) continue;
        state.messages.push(normalizeMessageItem({ ...item, type: normalizedType }));
        existing.add(key);
      }
      syncMessageCount();
    }

    function isMessageListLocked() {
      return Boolean(document.querySelector("#messageList .message-item.is-resolving, #messageList .message-item.is-resolved, #messageList .message-item.is-dismissing"));
    }

    function bindMessageProbeActions(msg, el) {
      el.querySelectorAll("[data-probe]").forEach((button) => {
        button.addEventListener("click", () => respondProbe(msg, button.dataset.probe, el));
      });
    }

    function renderMessages() {
      const list = $("#messageList");
      if (state.messageListDomLocked || isMessageListLocked()) {
        syncMessageCount();
        return;
      }
      const source = state.messageListSnapshot && isMessagesDrawerOpen() ? state.messageListSnapshot : state.messages;
      const messages = getRenderableMessages(source);
      if (state.messageListSnapshot && isMessagesDrawerOpen()) state.messageListSnapshot = messages;
      else state.messages = messages;
      syncMessageCount();
      if (!messages.length) {
        list.innerHTML = `<div class="empty-state">暂无通知。兴趣确认、避雷确认和待通知候选都会出现在这里。</div>`;
        return;
      }
      list.replaceChildren(...messages.map((msg) => {
        const el = document.createElement("article");
        const key = messageKey(msg);
        const resolvedResult = state.resolvedMessageResults.get(key);
        el.className = "message-item";
        el.dataset.messageKey = key;
        if (messageType(msg) === "web-monitor-notification") {
          el.classList.add("is-notification", "is-monitor-notification");
          const eyebrow = msg.kind === "failure" ? "监控需要处理" : msg.kind === "recovered" ? "监控已恢复" : "网页正文变化";
          const primaryLabel = msg.kind === "change" ? "查看事件" : "打开监控";
          el.innerHTML = `<p class="eyebrow">${escapeHtml(eyebrow)}</p><h3>${escapeHtml(msg.title)}</h3><p class="video-meta">${escapeHtml(msg.reason)}</p><div class="message-note">${escapeHtml(webMonitorTime(msg.created_at, "刚刚"))} · 来自本机持久监控</div><div class="message-card-actions"><button class="small-btn" data-monitor-message="dismiss" type="button">忽略</button><div class="message-primary-actions"><button class="small-btn" data-monitor-message="open" type="button">${primaryLabel}</button></div></div>`;
          el.querySelectorAll("[data-monitor-message]").forEach((button) => {
            button.addEventListener("click", () => void respondWebMonitorNotification(msg, button.dataset.monitorMessage, el));
          });
        } else if (messageType(msg) === "notification") {
          el.classList.add("is-notification");
          const viewAction = msg.content_url
            ? `<a class="small-btn" data-notification-msg="view" href="${escapeHtml(msg.content_url)}" target="_blank" rel="noopener noreferrer">阅读原文</a>`
            : `<button class="small-btn" data-notification-msg="view" type="button">阅读原文</button>`;
          el.innerHTML = `<p class="eyebrow">待通知候选</p><h3>${escapeHtml(msg.title)}</h3><p class="video-meta">${escapeHtml(msg.reason)}</p><div class="message-note">这类消息来自后端挑出的高置信推荐，用于插件通知；标记已通知后不会反复出现。</div><div class="message-card-actions"><div class="card-feedback-icons" aria-label="通知候选状态"><button class="feedback-icon-btn" data-notification-msg="dismiss" type="button" aria-label="标记已通知" title="标记已通知"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg></button></div><div class="message-primary-actions">${viewAction}</div></div>`;
          el.querySelectorAll("[data-notification-msg]").forEach((btn) => {
            btn.addEventListener("click", () => respondNotification(msg, btn.dataset.notificationMsg, el));
            btn.addEventListener("auxclick", (event) => {
              if (event.button === 1 && btn.dataset.notificationMsg === "view") {
                respondNotification(msg, "view", el);
              }
            });
          });
        } else {
          const isAvoidance = messageType(msg) === "avoidance.probe";
          const isChallenge = !isAvoidance && isChallengeProbe(msg);
          el.classList.add(isAvoidance ? "is-avoidance-probe" : isChallenge ? "is-challenge-probe" : "is-interest-probe");
          const eyebrow = isAvoidance ? "避雷确认" : isChallenge ? "挑战探针" : "兴趣确认";
          const actionsLabel = isAvoidance ? "确认或排除这个避雷方向" : isChallenge ? "确认或排除这个挑战方向" : "确认或排除这个兴趣";
          const kindCopy = isAvoidance
            ? "想少看这类，就确认这是雷点；如果新闻爪猜错了，点不是。"
            : isChallenge
              ? "这是挑战方向，会把口味往侧边推一点；想继续试探就点喜欢，不准就点不喜欢。"
            : "想继续探索这个方向，就点喜欢；不准就点不喜欢。";
          const actionCopy = probeActionCopy(messageType(msg));
          const actionButtons = `
            <button class="probe-btn is-confirm" data-probe="confirm" type="button">${actionCopy.confirm}</button>
            <button class="probe-btn is-neutral" data-probe="defer" type="button">${actionCopy.defer}</button>
            <button class="probe-btn is-reject" data-probe="reject" type="button">${actionCopy.reject}</button>`;
          el.innerHTML = `<p class="eyebrow">${eyebrow}</p><div class="message-note probe-kind-copy">${escapeHtml(kindCopy)}</div><h3>${escapeHtml(msg.domain)}</h3><p class="video-meta">${escapeHtml(msg.reason)}</p><div class="profile-chip-row">${asArray(msg.specifics).map((s) => `<span class="chip">${escapeHtml(s)}</span>`).join("")}</div><div class="message-card-actions"><div class="card-feedback-icons" aria-label="${actionsLabel}">${actionButtons}</div><div class="message-primary-actions"><button class="small-btn" data-probe="chat">${actionCopy.chat}</button></div></div>`;
          if (resolvedResult) {
            el.classList.add("is-resolved");
            const resolvedActions = el.querySelector(".message-card-actions");
            if (resolvedActions) resolvedActions.outerHTML = `<div class="message-note is-success">${escapeHtml(resolvedResult)}</div>`;
          } else {
            bindMessageProbeActions(msg, el);
          }
        }
        return el;
      }));
    }

    async function respondNotification(msg, response, el) {
      if (response === "view" && msg.content_url) trackRecommendationClick(msg);
      await requestJson(ENDPOINTS.notificationSent, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bvid: msg.bvid }) });
      state.messages = state.messages.filter((item) => !(messageType(item) === "notification" && String(item.bvid) === String(msg.bvid)));
      renderMessages();
      if (el) el.remove();
      showToast(response === "view" ? "已打开并标记这条通知" : "已标记这条通知");
    }

    async function refreshWebMonitorNotifications() {
      if (state.newsclawMode !== true) return;
      const generation = ++state.webMonitorNotificationGeneration;
      try {
        const payload = await requestJsonStrict(ENDPOINTS.webMonitorNotifications, {
          timeoutMs: 15000,
          cache: "no-store",
        });
        if (
          state.newsclawMode !== true
          || generation !== state.webMonitorNotificationGeneration
        ) return;
        const incoming = asArray(payload?.items).map((item) => ({
          ...item,
          type: "web-monitor-notification",
        }));
        state.messages = state.messages.filter(
          (item) => messageType(item) !== "web-monitor-notification"
        );
        mergeMessages(incoming);
        await Promise.allSettled(incoming
          .filter((item) => item.delivery_state === "pending")
          .map((item) => requestJsonStrict(
            `${ENDPOINTS.webMonitorNotifications}/${encodeURIComponent(item.delivery_id)}`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                action: "delivered",
                request_id: `web-monitor:delivery:${String(item.delivery_id || "")}`,
              }),
              timeoutMs: 15000,
            }
          )));
      } catch {
        // Keep already rendered durable rows; the next foreground refresh will
        // pull the same delivery ids again instead of inventing an empty inbox.
      }
    }

    async function respondWebMonitorNotification(msg, response, el) {
      const deliveryId = String(msg.delivery_id || "");
      if (!deliveryId || state.webMonitorNotificationActions.has(deliveryId)) return;
      state.webMonitorNotificationActions.add(deliveryId);
      const modeGeneration = state.newsclawModeGeneration;
      const action = response === "dismiss" ? "dismiss" : "read";
      try {
        await requestJsonStrict(
          `${ENDPOINTS.webMonitorNotifications}/${encodeURIComponent(msg.delivery_id)}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              action,
              request_id: `web-monitor:${action}:${String(msg.delivery_id || "")}`,
            }),
            timeoutMs: 15000,
          }
        );
        state.webMonitorNotificationGeneration += 1;
        state.messages = state.messages.filter(
          (item) => messageKey(item) !== messageKey(msg)
        );
        if (state.messageListSnapshot) {
          state.messageListSnapshot = state.messageListSnapshot.filter(
            (item) => messageKey(item) !== messageKey(msg)
          );
        }
        syncMessageCount();
        renderMessages();
        el?.remove();
        if (
          state.newsclawMode !== true
          || modeGeneration !== state.newsclawModeGeneration
        ) return;
        if (response === "open") {
          if (msg.kind === "change" && msg.event_id) {
            openHomePage("events");
            await eventWorkspaceController?.openEvent?.(msg.event_id, msg.change_id);
          } else {
            openContentLibraryPage("monitoring", { forceLoad: true });
          }
        }
        void refreshWebMonitors({ silent: true });
      } catch (error) {
        showToast(configErrorMessage(error?.details) || error?.message || "通知状态保存失败");
      } finally {
        state.webMonitorNotificationActions.delete(deliveryId);
      }
    }

    function collapseMessageItem(key, fallbackEl, onDone) {
      const target = fallbackEl?.isConnected ? fallbackEl : Array.from(document.querySelectorAll("#messageList .message-item")).find((item) => item.dataset.messageKey === key);
      const finish = () => { onDone?.(); };
      if (!target) {
        finish();
        return;
      }
      target.style.height = `${target.getBoundingClientRect().height}px`;
      target.style.minHeight = "0px";
      target.style.overflow = "hidden";
      target.style.transition = `height 240ms var(--ease-standard), opacity 180ms var(--ease-standard), padding 240ms var(--ease-standard), border-width 240ms var(--ease-standard)`;
      target.getBoundingClientRect();
      target.classList.add("is-dismissing");
      target.style.height = "0px";
      window.setTimeout(() => {
        target.remove();
        finish();
      }, 260);
    }

    function appendInlineChatBubble(container, role, text) {
      if (!container) return null;
      const bubble = document.createElement("div");
      bubble.className = `inline-chat-bubble ${role}${role === "reply" ? " inline-chat-reply" : ""}`;
      if (role === "reply") {
        bubble.classList.add("chat-markdown");
        bubble.innerHTML = renderMarkdown(text);
      } else bubble.textContent = text;
      container.appendChild(bubble);
      return bubble;
    }

    function messageProbeChatPrompt(msg, isAvoidance) {
      return msg.domain
        ? `我想多聊聊「${msg.domain}」这个${isAvoidance ? "避雷" : "兴趣"}方向。`
        : `我想多聊聊这个${isAvoidance ? "避雷" : "兴趣"}方向。`;
    }

    async function pollInlineMessageChatTurn(turnId, chatArea, thinking, startedAt = Date.now()) {
      const showReply = (text, tone = "reply") => {
        thinking?.remove();
        appendInlineChatBubble(chatArea.querySelector(".inline-chat-turns"), tone, text);
        chatArea.querySelectorAll(".inline-chat-input, .inline-chat-send, .inline-chat-cancel").forEach((control) => { control.disabled = false; });
        chatArea.querySelector(".inline-chat-input")?.focus();
      };
      try {
        const latest = await requestJson(`${ENDPOINTS.chatTurns}/${encodeURIComponent(turnId)}`);
        if (latest?.status === "failed" || Date.now() - startedAt > 180000) {
          showReply(latest?.error || "聊天处理超时，稍后可以在历史里继续查看。", "error");
          return;
        }
        if (latest?.status === "completed" || latest?.reply) {
          showReply(latest.reply || "后端已完成这轮聊天。");
          return;
        }
      } catch {
        // Keep polling below; transient disconnects should not collapse the inline composer.
      }
      window.setTimeout(() => pollInlineMessageChatTurn(turnId, chatArea, thinking, startedAt), 1200);
    }

    function openInlineMessageProbeChat(msg, el) {
      if (!el) return;
      const existing = el.querySelector(".inline-chat-area");
      if (existing) {
        existing.querySelector(".inline-chat-input")?.focus();
        return;
      }
      const probeType = messageType(msg);
      const isAvoidance = probeType === "avoidance.probe";
      const domain = String(msg.domain || "");
      const prompt = messageProbeChatPrompt(msg, isAvoidance);
      const actions = el.querySelector(".message-card-actions");
      if (actions) actions.hidden = true;
      const chatArea = document.createElement("div");
      chatArea.className = "inline-chat-area";
      chatArea.innerHTML = `
        <div class="inline-chat-turns" aria-live="polite"></div>
        <div class="inline-chat-compose">
          <textarea class="inline-chat-input" rows="2" placeholder="${escapeHtml(isAvoidance ? `聊聊你为什么想避开「${domain || "这个方向"}」…` : `聊聊你对「${domain || "这个方向"}」的想法…`)}"></textarea>
          <button class="inline-chat-send" type="button">发送</button>
          <button class="inline-chat-cancel" type="button">返回</button>
        </div>`;
      actions?.insertAdjacentElement("afterend", chatArea);
      const input = chatArea.querySelector(".inline-chat-input");
      const sendBtn = chatArea.querySelector(".inline-chat-send");
      const cancelBtn = chatArea.querySelector(".inline-chat-cancel");
      const closeComposer = () => {
        chatArea.remove();
        if (actions) actions.hidden = false;
      };
      const submit = async () => {
        const message = input?.value?.trim() || "";
        if (!message) {
          input?.focus();
          return;
        }
        chatArea.querySelectorAll(".inline-chat-input, .inline-chat-send, .inline-chat-cancel").forEach((control) => { control.disabled = true; });
        appendInlineChatBubble(chatArea.querySelector(".inline-chat-turns"), "user", message);
        const thinking = appendInlineChatBubble(chatArea.querySelector(".inline-chat-turns"), "thinking", "新闻爪正在结合这条探针思考…");
        const turnId = createClientTurnId(isAvoidance ? "avoidance-probe" : "probe");
        try {
          const turn = await requestJsonStrict(ENDPOINTS.chatTurns, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              turn_id: turnId,
              session: SHARED_CHAT_SESSION,
              scope: isAvoidance ? "avoidance_probe" : "probe",
              subject_id: domain,
              subject_title: domain || (isAvoidance ? "这个避雷方向" : "这个兴趣方向"),
              message: `${prompt}\n\n${message}`
            })
          });
          if (input) input.value = "";
          void pollInlineMessageChatTurn(turn?.turn_id || turnId, chatArea, thinking);
        } catch (error) {
          thinking?.remove();
          appendInlineChatBubble(chatArea.querySelector(".inline-chat-turns"), "error", error?.message || "后台正忙，等一下再聊。");
          chatArea.querySelectorAll(".inline-chat-input, .inline-chat-send, .inline-chat-cancel").forEach((control) => { control.disabled = false; });
          input?.focus();
        }
      };
      sendBtn?.addEventListener("click", () => void submit());
      cancelBtn?.addEventListener("click", closeComposer);
      input?.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
          event.preventDefault();
          void submit();
        }
        if (event.key === "Escape") closeComposer();
      });
      window.setTimeout(() => input?.focus(), 40);
    }

    function probePendingKey(type, domain) {
      const normalizedDomain = String(domain || "").trim().toLowerCase();
      return normalizedDomain ? `probe:${messageType({ type })}:${normalizedDomain}` : "";
    }

    function submitProbeResponse(type, domain, response, { surface = "", keepalive = false } = {}) {
      const isAvoidance = isAvoidanceProbe(type);
      const endpoint = isAvoidance ? ENDPOINTS.avoidanceProbeRespond : ENDPOINTS.interestProbeRespond;
      const payload = { domain, response, message: "" };
      if (!isAvoidance && surface) payload.surface = surface;
      return requestJsonStrict(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        keepalive
      }).then((apiResponse) => {
        if (apiResponse?.ok === false) throw new Error("后端未接受这次探针反馈");
        return apiResponse;
      });
    }

    function probeFeedbackMessage(type, response, domain, apiResponse = null) {
      const raw = String(domain || apiResponse?.domain || "这个方向").replace(/\s+/g, " ").trim();
      const subject = raw.length > 24 ? `${raw.slice(0, 23)}…` : raw;
      const quoted = `「${subject || "这个方向"}」`;
      const avoidance = type === "avoidance.probe";
      if (response === "confirm") return avoidance ? `已确认避雷${quoted}` : `已确认兴趣${quoted}`;
      if (response === "defer") {
        if (apiResponse?.action === "defer_exhausted") {
          return avoidance ? `已搁置避雷${quoted}，之后先不提` : `已搁置兴趣${quoted}，之后先不提`;
        }
        return avoidance
          ? `已搁置避雷${quoted}，过阵子可能再提`
          : `已搁置兴趣${quoted}，过阵子可能再提`;
      }
      return avoidance ? `已排除避雷${quoted}` : `已排除兴趣${quoted}`;
    }

    function respondProbe(msg, response, el) {
      if (!el) return;
      const actions = el.querySelector(".message-card-actions");
      if (response === "chat") {
        openInlineMessageProbeChat(msg, el);
        showToast("已在这条消息里打开聊天输入");
        return;
      }
      if (!actions) return;
      const stateKey = messageKey(msg);
      const probeType = messageType(msg);
      const domain = msg.domain || "";
      const handledKey = probeKey(probeType, domain);
      const pendingKey = probePendingKey(probeType, domain);
      const snapshot = {
        actionsHtml: actions.innerHTML,
        actionsClass: actions.className,
        minHeight: el.style.minHeight
      };
      let apiResponse = null;
      const scheduled = pendingActions.schedule(pendingKey, {
        commit: ({ keepalive }) => {
          if (el.isConnected && !keepalive) {
            el.classList.remove("is-feedback-pending");
            el.classList.add("is-feedback-saving");
            actions.innerHTML = '<div class="message-action-result">正在保存反馈…</div>';
          }
          return submitProbeResponse(probeType, domain, response, { keepalive }).then((result) => {
            apiResponse = result;
            return result;
          });
        },
        rollback: ({ reason }) => {
          if (handledKey) state.handledProbeKeys.delete(handledKey);
          state.resolvingMessageKeys.delete(stateKey);
          state.messageListDomLocked = state.resolvingMessageKeys.size > 0;
          state.resolvedMessageResults.delete(stateKey);
          el.classList.remove("is-feedback-pending", "is-feedback-saving", "is-feedback-committed");
          el.style.minHeight = snapshot.minHeight;
          actions.className = snapshot.actionsClass;
          actions.innerHTML = snapshot.actionsHtml;
          bindMessageProbeActions(msg, el);
          if (reason === "undo") showToast("已撤销探针反馈");
        },
        committed: () => {
          const result = probeFeedbackMessage(probeType, response, domain, apiResponse);
          state.resolvedMessageResults.set(stateKey, result);
          state.resolvingMessageKeys.delete(stateKey);
          state.messageListDomLocked = state.resolvingMessageKeys.size > 0;
          state.messages = state.messages.filter((item) => messageKey(item) !== stateKey);
          if (state.messageListSnapshot) state.messageListSnapshot = state.messageListSnapshot.filter((item) => messageKey(item) !== stateKey);
          syncMessageCount();
          if (!el.isConnected) return;
          el.classList.remove("is-feedback-pending", "is-feedback-saving");
          el.classList.add("is-feedback-committed");
          actions.classList.add("is-result");
          const resultNode = document.createElement("div");
          resultNode.className = "message-action-result";
          resultNode.title = result;
          resultNode.textContent = result;
          actions.replaceChildren(resultNode);
          showToast(result);
        }
      });
      if (!scheduled) {
        showToast("这条探针反馈正在处理中。");
        return;
      }

      state.messageListDomLocked = true;
      if (!state.messageListSnapshot && isMessagesDrawerOpen()) state.messageListSnapshot = getRenderableMessages();
      state.resolvingMessageKeys.add(stateKey);
      if (handledKey) state.handledProbeKeys.add(handledKey);
      el.style.minHeight = `${el.getBoundingClientRect().height}px`;
      el.classList.add("is-feedback-pending");
      const result = probeFeedbackMessage(probeType, response, domain);
      actions.classList.add("is-result");
      const resultNode = document.createElement("div");
      resultNode.className = "message-action-result";
      resultNode.textContent = `${result} `;
      const undoButton = document.createElement("button");
      undoButton.className = "feedback-undo-btn";
      undoButton.setAttribute("data-probe-undo", "");
      undoButton.type = "button";
      undoButton.textContent = "撤销";
      undoButton.addEventListener("click", () => { pendingActions.undo(pendingKey); });
      resultNode.appendChild(undoButton);
      actions.replaceChildren(resultNode);
    }

    function bindSpeculativeRowActions(row) {
      row.querySelectorAll("[data-spec-response]").forEach((button) => {
        button.addEventListener("click", () => respondSpeculativeInterest(button));
      });
    }

    function bindSpeculativeActions() {
      document.querySelectorAll("[data-spec-domain]").forEach(bindSpeculativeRowActions);
    }

    function respondSpeculativeInterest(button) {
      const row = button.closest("[data-spec-domain]");
      const domain = row?.dataset.specDomain;
      const response = button.dataset.specResponse;
      if (!domain || !response) return;
      const type = button.dataset.specType || "interest.probe";
      const handledKey = probeKey(type, domain);
      const pendingKey = probePendingKey(type, domain);
      const actions = row.querySelector(".spec-actions");
      if (!actions) return;
      const snapshot = {
        actionsHtml: actions.innerHTML,
        actionsClass: actions.className
      };
      let apiResponse = null;
      const scheduled = pendingActions.schedule(pendingKey, {
        commit: ({ keepalive }) => {
          if (row.isConnected && !keepalive) {
            row.classList.remove("is-feedback-pending");
            row.classList.add("is-feedback-saving");
            actions.innerHTML = '<p class="spec-result">正在保存反馈…</p>';
          }
          return submitProbeResponse(type, domain, response, { surface: "profile", keepalive }).then((result) => {
            apiResponse = result;
            return result;
          });
        },
        rollback: ({ reason }) => {
          if (handledKey) state.handledProbeKeys.delete(handledKey);
          row.classList.remove("is-feedback-pending", "is-feedback-saving", "is-feedback-committed");
          actions.className = snapshot.actionsClass;
          actions.innerHTML = snapshot.actionsHtml;
          bindSpeculativeRowActions(row);
          if (reason === "undo") showToast("已撤销探针反馈");
        },
        committed: () => {
          const result = probeFeedbackMessage(type, response, domain, apiResponse);
          const messageStateKey = probeKey(type, domain);
          state.messages = state.messages.filter((msg) => messageKey(msg) !== messageStateKey);
          if (state.messageListSnapshot) state.messageListSnapshot = state.messageListSnapshot.filter((msg) => messageKey(msg) !== messageStateKey);
          syncMessageCount();
          if (!row.isConnected) return;
          row.classList.remove("is-feedback-pending", "is-feedback-saving");
          row.classList.add("is-feedback-committed");
          const resultNode = document.createElement("p");
          resultNode.className = "spec-result";
          resultNode.textContent = result;
          actions.replaceChildren(resultNode);
          showToast(result);
        }
      });
      if (!scheduled) {
        showToast("这条探针反馈正在处理中。");
        return;
      }

      if (handledKey) state.handledProbeKeys.add(handledKey);
      row.classList.add("is-feedback-pending");
      const result = probeFeedbackMessage(type, response, domain);
      const resultNode = document.createElement("p");
      resultNode.className = "spec-result";
      resultNode.textContent = `${result} `;
      const undoButton = document.createElement("button");
      undoButton.className = "feedback-undo-btn";
      undoButton.setAttribute("data-probe-undo", "");
      undoButton.type = "button";
      undoButton.textContent = "撤销";
      undoButton.addEventListener("click", () => { pendingActions.undo(pendingKey); });
      resultNode.appendChild(undoButton);
      actions.replaceChildren(resultNode);
    }

    function createClientTurnId(prefix = "webui") {
      const suffix = window.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
      return `${prefix}-${suffix}`;
    }

    function normalizeDelightTurn(turn) {
      if (!turn) return null;
      const message = String(turn.message ?? turn.user_message ?? "");
      const reply = String(turn.reply ?? turn.assistant_message ?? "");
      const status = String(turn.status || (reply ? "completed" : "pending"));
      const turnId = String(turn.turn_id ?? turn.id ?? "");
      if (!turnId && !message && !reply) return null;
      return {
        turn_id: turnId,
        message,
        reply,
        status,
        error: String(turn.error ?? "")
      };
    }

    function delightTurnList(turns) {
      return asArray(turns).map(normalizeDelightTurn).filter(Boolean);
    }

    function upsertDelightTurn(turns, nextTurn) {
      const normalized = normalizeDelightTurn(nextTurn);
      const existing = delightTurnList(turns);
      if (!normalized) return existing;
      const index = existing.findIndex((turn) => turn.turn_id && turn.turn_id === normalized.turn_id);
      if (index < 0) return [...existing, normalized];
      return existing.map((turn, turnIndex) => turnIndex === index ? normalized : turn);
    }

    function mergeDelightTurnLists(currentTurns, incomingTurns) {
      let merged = delightTurnList(currentTurns);
      for (const turn of delightTurnList(incomingTurns)) merged = upsertDelightTurn(merged, turn);
      return merged;
    }

    function mergeDelightItem(current, incoming) {
      if (!current) return incoming;
      return {
        ...current,
        ...incoming,
        chat_turn_id: incoming.chat_turn_id || current.chat_turn_id || "",
        chat_reply: incoming.chat_reply || current.chat_reply || "",
        chat_draft: incoming.chat_draft || current.chat_draft || "",
        response_message: incoming.response_message || current.response_message || "",
        turns: mergeDelightTurnLists(current.turns, incoming.turns)
      };
    }

    function renderDelightTurns(delight) {
      const area = $("#delightTurns");
      if (!area) return;
      area.replaceChildren();
      const turns = delightTurnList(delight?.turns);
      if (!turns.length && !delight?.chat_reply) {
        area.hidden = true;
        scheduleActivityRailHeightSync();
        return;
      }
      area.hidden = false;
      if (!turns.length && delight?.chat_reply) {
        const bubble = document.createElement("div");
        bubble.className = "delight-turn-bubble is-assistant chat-markdown";
        bubble.innerHTML = renderMarkdown(delight.chat_reply);
        area.append(bubble);
        scheduleActivityRailHeightSync();
        return;
      }
      for (const turn of turns) {
        if (turn.message) {
          const userBubble = document.createElement("div");
          userBubble.className = "delight-turn-bubble is-user";
          userBubble.textContent = turn.message;
          area.append(userBubble);
        }
        const assistantBubble = document.createElement("div");
        const status = String(turn.status || "pending");
        assistantBubble.className = `delight-turn-bubble is-assistant${status === "pending" ? " is-thinking" : ""}${status === "failed" ? " is-error" : ""}`;
        const assistantText = status === "pending"
          ? "新闻爪正在理解你这句话…"
          : status === "failed"
            ? turn.error || "这句还没发出去，稍后再试。"
            : turn.reply || "后端已完成这轮聊天。";
        if (status === "pending") assistantBubble.textContent = assistantText;
        else {
          assistantBubble.classList.add("chat-markdown");
          assistantBubble.innerHTML = renderMarkdown(assistantText);
        }
        area.append(assistantBubble);
      }
      scheduleActivityRailHeightSync();
    }

    function updateDelightState(bvid, updates) {
      const key = String(bvid || "");
      if (!key) return null;
      let current = null;
      state.delights = state.delights.map((item) => {
        if (String(item.bvid || "") !== key) return item;
        current = { ...item, ...updates };
        return current;
      });
      if (state.delight && String(state.delight.bvid || "") === key) {
        state.delight = { ...state.delight, ...updates };
        current = state.delight;
      }
      if (current && state.delight && String(state.delight.bvid || "") === key) {
        renderDelightTurns(state.delight);
        if ($("#delightStatus")) {
          $("#delightStatus").setAttribute("role", "status");
          $("#delightStatus").textContent = state.delight.response_message || "";
        }
      }
      return current;
    }

    function delightContentUrl(delight) {
      if (!delight) return "";
      return contentUrl(delight);
    }

    function ensureDelightThumbAnchor() {
      const thumb = $("#delightThumb");
      if (!thumb || thumb.tagName.toLowerCase() === "a") return thumb;
      const anchor = document.createElement("a");
      Array.from(thumb.attributes).forEach((attr) => {
        if (attr.name !== "role" && attr.name !== "tabindex") {
          anchor.setAttribute(attr.name, attr.value);
        }
      });
      while (thumb.firstChild) anchor.append(thumb.firstChild);
      thumb.replaceWith(anchor);
      return anchor;
    }

    function syncDelightThumbLink(delight) {
      const thumb = ensureDelightThumbAnchor();
      if (!thumb) return null;
      const url = delightContentUrl(delight);
      if (url) {
        thumb.href = url;
        thumb.target = "_blank";
        thumb.rel = "noopener noreferrer";
        thumb.removeAttribute("role");
        thumb.removeAttribute("tabindex");
        thumb.removeAttribute("aria-disabled");
        thumb.setAttribute("aria-label", "打开当前今日首选原文");
        return thumb;
      }
      thumb.removeAttribute("href");
      thumb.removeAttribute("target");
      thumb.removeAttribute("rel");
      thumb.setAttribute("role", "img");
      thumb.setAttribute("tabindex", "-1");
      thumb.setAttribute("aria-disabled", "true");
      thumb.setAttribute("aria-label", "当前今日首选没有安全的原文链接");
      return thumb;
    }

    function applyTurnToDelight(turn) {
      const subjectId = String(turn?.subject_id || turn?.bvid || "");
      if (!turn || (turn.scope && turn.scope !== "delight") || !subjectId) return null;
      const existing = state.delights.find((item) => String(item.bvid || "") === subjectId)
        || (state.delight && String(state.delight.bvid || "") === subjectId ? state.delight : null);
      const entry = normalizeDelightTurn(turn);
      if (!entry) return null;
      const status = String(entry.status || "pending");
      const updates = {
        chat_turn_id: entry.turn_id,
        turns: upsertDelightTurn(existing?.turns, entry),
        response_message: status === "completed" ? "这句已经记下，后面会更会试探。" : status === "failed" ? "这句还没发出去，稍后再试。" : "新闻爪正在理解你这句话。"
      };
      if (status === "completed") {
        updates.chat_reply = entry.reply || existing?.chat_reply || "";
        updates.chat_draft = "";
      }
      return updateDelightState(subjectId, updates);
    }

    function pollChatTurnUntilSettled(turnId, fallbackTurn) {
      const startedAt = Date.now();
      const poll = async () => {
        const latest = await requestJson(`${ENDPOINTS.chatTurns}/${encodeURIComponent(turnId)}`);
        if (latest) {
          const scopedTurn = { ...fallbackTurn, ...latest, scope: latest.scope || "delight", subject_id: latest.subject_id || fallbackTurn.subject_id };
          applyTurnToDelight(scopedTurn);
          if (latest.status === "completed" || latest.status === "failed") return;
        }
        if (Date.now() - startedAt > 180000) {
          applyTurnToDelight({ ...fallbackTurn, status: "failed", error: "聊天处理超时，稍后可以在历史里继续查看。" });
          return;
        }
        window.setTimeout(poll, 1200);
      };
      window.setTimeout(poll, 1200);
    }

    async function respondDelight(delight, response, el = null, openUrl = false) {
      if (response === "retry") {
        if (state.delightLoadState === "loading") return;
        await fetchDelightQueue();
        return;
      }
      if (!delight) return;
      if (response === "chat") { openDelightComposer(); return; }
      if (response === "cancel-comment") { closeDelightComposer(); return; }
      if (response === "watch-later") {
        const btn = document.querySelector('[data-delight="watch-later"]');
        const savedItem = desktopSavedItem(delight);
        if (!btn || btn.disabled || desktopSavedMutations.isBusy("watch_later", savedItem.item_key)) return;
        btn.disabled = true;
        const wasSaved = desktopSavedMutations.isSaved("watch_later", savedItem.item_key);
        btn.setAttribute("aria-pressed", wasSaved ? "false" : "true");
        _delightStatusCache.set(savedItem.item_key, { ...(_delightStatusCache.get(savedItem.item_key) || {}), watchLater: !wasSaved });
        if ($("#delightStatus")) { $("#delightStatus").removeAttribute("role"); $("#delightStatus").textContent = wasSaved ? "正在从本地稍后读移除…" : "正在保存到本地稍后读…"; }
        try {
          await desktopSavedMutations.toggle("watch_later", savedItem.item_key, {
            add: () => saveDesktopItem("watch_later", savedItem),
            remove: () => removeDesktopSavedItem("watch_later", savedItem.item_key)
          });
          publishSavedChange("watch_later", savedItem.item_key, desktopSavedMutations.isSaved("watch_later", savedItem.item_key));
          if ($("#delightStatus")) $("#delightStatus").textContent = wasSaved ? "已从本机稍后读移除。" : "已保存到本机稍后读。";
        } catch (error) {
          btn.setAttribute("aria-pressed", wasSaved ? "true" : "false");
          _delightStatusCache.set(savedItem.item_key, { ...(_delightStatusCache.get(savedItem.item_key) || {}), watchLater: wasSaved });
          if ($("#delightStatus")) { $("#delightStatus").setAttribute("role", "alert"); $("#delightStatus").textContent = error?.message || "本地稍后读操作失败，请重试。"; }
        } finally {
          btn.disabled = false;
        }
        return;
      }
      if (response === "favorite") {
        const btn = document.querySelector('[data-delight="favorite"]');
        const savedItem = desktopSavedItem(delight);
        if (!btn || btn.disabled || desktopSavedMutations.isBusy("favorite", savedItem.item_key)) return;
        btn.disabled = true;
        const wasSaved = desktopSavedMutations.isSaved("favorite", savedItem.item_key);
        btn.setAttribute("aria-pressed", wasSaved ? "false" : "true");
        _delightStatusCache.set(savedItem.item_key, { ...(_delightStatusCache.get(savedItem.item_key) || {}), favorite: !wasSaved });
        if ($("#delightStatus")) { $("#delightStatus").removeAttribute("role"); $("#delightStatus").textContent = wasSaved ? "正在从本地收藏移除…" : "正在保存到本地收藏…"; }
        try {
          await desktopSavedMutations.toggle("favorite", savedItem.item_key, {
            add: () => saveDesktopItem("favorite", savedItem),
            remove: () => removeDesktopSavedItem("favorite", savedItem.item_key)
          });
          publishSavedChange("favorite", savedItem.item_key, desktopSavedMutations.isSaved("favorite", savedItem.item_key));
          if ($("#delightStatus")) $("#delightStatus").textContent = wasSaved ? "已从本机收藏移除。" : "已保存到本机收藏。";
        } catch (error) {
          btn.setAttribute("aria-pressed", wasSaved ? "true" : "false");
          _delightStatusCache.set(savedItem.item_key, { ...(_delightStatusCache.get(savedItem.item_key) || {}), favorite: wasSaved });
          if ($("#delightStatus")) { $("#delightStatus").setAttribute("role", "alert"); $("#delightStatus").textContent = error?.message || "本地收藏操作失败，请重试。"; }
        } finally {
          btn.disabled = false;
        }
        return;
      }
      if (response === "send-comment") {
        if (delightFeedbackInFlight) return;
        const input = $("#delightCommentInput");
        const note = input?.value?.trim() || "";
        if (!note) {
          if ($("#delightStatus")) $("#delightStatus").textContent = "先写一句想聊的内容，再提交这轮对话。";
          input?.focus();
          return;
        }
        const turnId = createClientTurnId("delight");
        const pendingTurn = { turn_id: turnId, session: SHARED_CHAT_SESSION, scope: "delight", subject_id: delight.bvid, subject_title: delight.title || "", message: note, reply: "", status: "pending", error: "" };
        applyTurnToDelight(pendingTurn);
        if (input) input.value = "";
        closeDelightComposer();
        delightFeedbackInFlight = true;
        syncDelightControlAvailability();
        try {
          const turn = await requestJsonStrict(ENDPOINTS.chatTurns, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(pendingTurn) });
          const scopedTurn = { ...pendingTurn, ...(turn || {}), scope: turn?.scope || "delight", subject_id: turn?.subject_id || delight.bvid };
          applyTurnToDelight(scopedTurn);
          if (scopedTurn.turn_id && scopedTurn.status !== "completed" && scopedTurn.status !== "failed") pollChatTurnUntilSettled(scopedTurn.turn_id, scopedTurn);
          showToast("已提交聊天线索");
        } catch (error) {
          applyTurnToDelight({ ...pendingTurn, status: "failed", error: error.message || "聊天提交失败，请稍后再试。" });
          if (input) input.value = note;
          showToast(`聊天提交失败：${error.message || "后端不可用"}`);
        } finally {
          delightFeedbackInFlight = false;
          syncDelightControlAvailability();
        }
        return;
      }
      if (response === "view") {
        const url = delightContentUrl(delight);
        // 「阅读原文」按钮是纯 <button>（不是封面那个 <a>），必须在这里显式打开，
        // 否则点了只弹 toast 却什么都不开（field report 2026-07-07）。封面缩略图
        // 已是带 href 的 <a> 靠原生导航打开，openUrl=false 不重复开、避免双开。
        // window.open 在点击手势的同步栈内调用，不会被拦截。
        if (!url) {
          if ($("#delightStatus")) $("#delightStatus").textContent = "后端没有返回可打开的 HTTP(S) 原文链接。";
          return;
        }
        if (delightFeedbackInFlight) return;
        delightFeedbackInFlight = true;
        syncDelightControlAvailability();
        if (openUrl) window.open(url, "_blank", "noopener,noreferrer");
        const status = $("#delightStatus");
        if (status) status.textContent = "原文已打开，正在记录这次主动选择…";
        let clickRecorded = false;
        try {
          [clickRecorded] = await Promise.all([
            trackRecommendationClick(delight),
            requestJsonStrict(ENDPOINTS.delightRespond, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ bvid: delight.bvid, response: "view", title: delight.title || "", message: "" })
            }).then(() => true, () => false),
          ]);
        } finally {
          delightFeedbackInFlight = false;
          syncDelightControlAvailability();
        }
        if (status) {
          status.textContent = clickRecorded
            ? "原文已打开，这次主动选择已记录。"
            : "原文已打开，但点击信号未记录成功；不会把它宣称为已学习。";
          if (!clickRecorded) status.setAttribute("role", "alert");
        }
        showToast("已打开今日首选原文");
        return;
      }
      const feedbackToast = response === "like" ? "惊喜推荐已喜欢" : response === "dislike" ? "这类惊喜先少来点" : "已标为看过，不再推荐";
      if (delightFeedbackInFlight) return;
      if (response === "like" && delight.state === "liked") {
        if ($("#delightStatus")) $("#delightStatus").textContent = "这条今日首选已经记录过喜欢，无需重复提交。";
        return;
      }
      delightFeedbackInFlight = true;
      syncDelightControlAvailability();
      let feedbackResult;
      try {
        const payload = {
          bvid: delight.bvid,
          response,
          title: delight.title,
          message: ""
        };
        feedbackResult = await requestJsonWithPendingId(
          ENDPOINTS.delightRespond,
          "delight-response",
          JSON.stringify([delight.bvid, response]),
          payload
        );
      } catch (error) {
        // 失败不假装成功：保留当前卡片，下次可再试。
        showToast(response === "like" ? "这次喜欢还没记上，可以再试一次" : "这次还没记上，请再试一次");
        return;
      } finally {
        delightFeedbackInFlight = false;
        syncDelightControlAvailability();
      }
      if (feedbackResult == null) {
        showToast(response === "like" ? "这次喜欢还没记上，可以再试一次" : "这次还没记上，请再试一次");
        setActiveDelight(state.delightIndex);
        return;
      }
      if (response === "like") {
        updateDelightState(delight.bvid, {
          state: "liked",
          response_message: "好，这类多来点。",
        });
        setActiveDelight(state.delightIndex);
      }
      if (response === "dislike" || response === "dismiss") {
        state.delights = state.delights.filter((item) => item.bvid !== delight.bvid);
        setActiveDelight(Math.min(state.delightIndex, state.delights.length - 1));
        if (el) el.remove();
      }
      showToast(feedbackToast);
    }

    function openMessageChat(msg) {
      const drawer = $("#messagesDrawer");
      const panel = $("#messagesPanel");
      const view = $("#messageChatView");
      const input = $("#messageChatInput");
      state.messageScrollTop = panel?.scrollTop || 0;
      const type = messageType(msg);
      const isAvoidance = type === "avoidance.probe";
      state.messageChatDomain = msg.domain || "";
      state.messageChatScope = isAvoidance ? "avoidance_probe" : "probe";
      openPanel("messagesDrawer");
      drawer?.classList.add("is-chatting");
      if (view) view.hidden = false;
      const title = $("#messageChatTitle");
      const context = $("#messageChatContext");
      const prompt = msg.domain
        ? `我想多聊聊「${msg.domain}」这个${isAvoidance ? "避雷" : "兴趣"}方向。`
        : `我想多聊聊这个${isAvoidance ? "避雷" : "兴趣"}方向。`;
      state.messageChatPrompt = prompt;
      state.messageChatSubjectTitle = msg.domain || (isAvoidance ? "这个避雷方向" : "这个兴趣方向");
      if (title) title.textContent = msg.domain ? `聊聊${isAvoidance ? "避雷" : "兴趣"}「${msg.domain}」` : `聊聊这个${isAvoidance ? "避雷" : "兴趣"}`;
      if (context) context.textContent = msg.reason || `这轮对话会沿用消息里的${isAvoidance ? "避雷" : "兴趣"}上下文。`;
      if (input) {
        input.value = "";
        input.placeholder = "继续写你想补充的问题、偏好或例子";
      }
      renderChat({ forceBottom: true });
      if (panel) panel.scrollTop = 0;
      window.setTimeout(() => input?.focus(), 80);
    }

    function returnToMessages() {
      const drawer = $("#messagesDrawer");
      const panel = $("#messagesPanel");
      const view = $("#messageChatView");
      drawer?.classList.remove("is-chatting");
      if (view) view.hidden = true;
      state.messageChatDomain = "";
      state.messageChatPrompt = "";
      state.messageChatScope = "probe";
      state.messageChatSubjectTitle = "";
      window.setTimeout(() => {
        if (panel) panel.scrollTop = state.messageScrollTop || 0;
      }, 0);
    }

    function desktopChatThinkingMarkup(
      label = "新闻爪正在思考，等待模型回复…"
    ) {
      return `<div class="chat-bubble agent chat-thinking" role="status" aria-live="polite" aria-atomic="true" aria-busy="true"><span class="chat-thinking-label">${escapeHtml(label)}</span><span class="chat-thinking-dots" aria-hidden="true"><span></span><span></span><span></span></span></div>`;
    }

    function desktopTurnIsWaitingForReply(turn) {
      if (!turn || isCardTurn(turn) || isQuestionTurn(turn)) return false;
      const status = String(turn.status || "").toLowerCase();
      const reply = String(turn.reply || turn.assistant_message || "").trim();
      return !reply && (status === "pending" || status === "processing");
    }

    function chatHtml(messages) {
      return messages.map((msg) => {
        if (msg?.turn) {
          const waiting = desktopTurnIsWaitingForReply(msg.turn)
            ? desktopChatThinkingMarkup()
            : "";
          return `${replyQuoteMarkup(msg.turn, desktopDialogueTurns())}${renderTurnMarkup(msg.turn, { surface: "desktop" })}${waiting}`;
        }
        if (msg?.thinking) return desktopChatThinkingMarkup(msg.text);
        const body = msg.role === "user"
          ? escapeHtml(msg.text)
          : `<div class="chat-markdown">${renderMarkdown(msg.text)}</div>`;
        return `<div class="chat-bubble ${msg.role === "user" ? "user" : "agent"}">${body}</div>`;
      }).join("");
    }

    function isNearScrollBottom(element) {
      return element.scrollHeight - element.clientHeight - element.scrollTop
        <= CHAT_SCROLL_BOTTOM_TOLERANCE_PX;
    }

    function openDialogueEvidenceTurnIds(element) {
      return new Set(
        Array.from(element.querySelectorAll(".dialogue-evidence[open]"))
          .map((details) => details.closest("[data-dialogue-turn-id]")?.dataset.dialogueTurnId || "")
          .filter(Boolean)
      );
    }

    function renderChatLogElement(element, markup, { forceBottom = false } = {}) {
      if (!element) return;
      const hadContent = element.childElementCount > 0;
      const shouldStickToBottom = forceBottom || !hadContent || isNearScrollBottom(element);
      const previousScrollTop = element.scrollTop;
      const openEvidenceTurnIds = openDialogueEvidenceTurnIds(element);

      element.innerHTML = markup;

      for (const details of element.querySelectorAll(".dialogue-evidence")) {
        const turnId = details.closest("[data-dialogue-turn-id]")?.dataset.dialogueTurnId || "";
        if (openEvidenceTurnIds.has(turnId)) details.open = true;
      }
      if (shouldStickToBottom) {
        element.scrollTop = element.scrollHeight;
      } else {
        element.scrollTop = Math.min(
          previousScrollTop,
          Math.max(0, element.scrollHeight - element.clientHeight)
        );
      }
    }

    function renderDesktopPendingConfirmations() {
      const pending = state.pendingConfirmations;
      const count = Math.max(0, Number(pending.count) || 0);
      updateSavedBadge("chatPendingCountBadge", count);
      const toggle = $("#desktopPendingToggle");
      const countLabel = $("#desktopPendingCount");
      const list = $("#desktopPendingConfirmations");
      if (countLabel) countLabel.textContent = count > 99 ? "99+" : String(count);
      if (toggle) {
        toggle.setAttribute("aria-expanded", String(Boolean(pending.expanded)));
        toggle.classList.toggle("is-expanded", Boolean(pending.expanded));
      }
      if (list) {
        const wasVisible = !list.hidden;
        const previousScrollTop = list.scrollTop;
        list.hidden = !pending.expanded;
        list.innerHTML = renderPendingListMarkup(pending.items);
        if (wasVisible && pending.expanded) {
          list.scrollTop = Math.min(
            previousScrollTop,
            Math.max(0, list.scrollHeight - list.clientHeight)
          );
        }
      }
    }

    function applyDialogueChatSnapshot(snapshot) {
      const items = selectDialogueTurns(Array.isArray(snapshot) ? snapshot : asArray(snapshot?.items));
      if (!items.length) return;
      const signature = JSON.stringify(items);
      if (signature === lastDialogueChatSignature) return;
      lastDialogueChatSignature = signature;
      state.chat = items.map((turn) => ({ turn }));
      renderChat();
    }

    async function refreshDialogueTurns() {
      const snapshot = await requestJsonStrict(
        `${ENDPOINTS.chatTurns}?session=${encodeURIComponent(SHARED_CHAT_SESSION)}&limit=100`,
        { cache: "no-store" }
      );
      applyDialogueChatSnapshot(snapshot);
    }

    async function fetchDesktopDialogueTurn(turnId, { signal, timeoutMs = 10000 } = {}) {
      const controller = new AbortController();
      const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
      const forwardAbort = () => controller.abort(signal?.reason);
      if (signal?.aborted) forwardAbort();
      else signal?.addEventListener("abort", forwardAbort, { once: true });
      try {
        return await requestJsonStrict(
          `${ENDPOINTS.chatTurns}/${encodeURIComponent(turnId)}`,
          { cache: "no-store", signal: controller.signal }
        );
      } finally {
        window.clearTimeout(timeoutId);
        signal?.removeEventListener("abort", forwardAbort);
      }
    }

    function desktopDialogueTurns() {
      return state.chat.map((entry) => entry?.turn).filter((turn) => turn && turn.turn_id);
    }

    function storeDialogueContext(selection) {
      dialogueContextSelection = writeContextSelection(
        (() => {
          try { return window.localStorage; } catch { return null; }
        })(),
        "desktop-web",
        selection,
      );
      return dialogueContextSelection;
    }

    async function validateDialogueContext({ announce = false } = {}) {
      const current = normalizeContextPreview(dialogueContextSelection);
      if (!current) return null;
      const contextTarget = desktopDialogueTurns().find(
        (turn) => turn.turn_id === current.reply_to_turn_id
      );
      if (isTerminalCardTurn(contextTarget)) {
        storeDialogueContext(clearContextSelection());
        return null;
      }
      try {
        const preview = normalizeContextPreview(await requestJsonStrict(
          `${ENDPOINTS.dialogueContexts}/${encodeURIComponent(current.reply_to_turn_id)}`,
          { cache: "no-store", timeoutMs: 5000 },
        ));
        if (!preview) throw new Error("invalid_context_preview");
        return storeDialogueContext(preview);
      } catch (error) {
        const code = contextErrorCode(error);
        if (["reply_target_not_found", "reply_target_inactive", "invalid_reply_target"].includes(code)) {
          storeDialogueContext(clearContextSelection());
          if (announce) showToast(contextErrorMessage(error));
        } else if (announce && code === "reply_target_processing") {
          showToast(contextErrorMessage(error));
        }
        return code === "reply_target_processing" ? current : null;
      }
    }

    async function selectDialogueContext(turnId, preview = null) {
      const target = desktopDialogueTurns().find((turn) => turn.turn_id === turnId) || { turn_id: turnId };
      const candidate = contextSelectionFromTurn(target, preview);
      if (candidate) {
        storeDialogueContext(candidate);
        renderDialogueContextBar();
        return candidate;
      }
      try {
        const fetched = normalizeContextPreview(await requestJsonStrict(
          `${ENDPOINTS.dialogueContexts}/${encodeURIComponent(turnId)}`,
          { cache: "no-store", timeoutMs: 5000 },
        ));
        const fetchedCandidate = contextSelectionFromTurn(target, fetched);
        if (!fetchedCandidate) throw new Error("invalid_context_preview");
        storeDialogueContext(fetchedCandidate);
        renderDialogueContextBar();
        return fetchedCandidate;
      } catch (error) {
        showToast(contextErrorMessage(error));
        return null;
      }
    }

    function renderDialogueContextBar() {
      const form = $("#chatForm");
      if (!form) return;
      let bar = $("#desktopDialogueContextBar");
      const markup = contextBarMarkup(dialogueContextSelection);
      if (!markup) {
        bar?.remove();
        return;
      }
      if (!bar) {
        bar = document.createElement("div");
        bar.id = "desktopDialogueContextBar";
        form.parentElement?.insertBefore(bar, form);
      }
      bar.innerHTML = markup;
      bar.querySelector("[data-context-clear]")?.addEventListener("click", () => {
        storeDialogueContext(clearContextSelection());
        showToast("已清除这条消息的对话上下文");
        renderDialogueContextBar();
      });
    }

    async function refreshDesktopPendingConfirmations() {
      const payload = await requestJsonStrict(
        `${ENDPOINTS.pendingConfirmations}?session=${encodeURIComponent(SHARED_CHAT_SESSION)}`,
        { cache: "no-store" }
      );
      state.pendingConfirmations = {
        ...state.pendingConfirmations,
        count: Math.max(0, Number(payload?.count) || 0),
        items: asArray(payload?.items)
      };
      renderDesktopPendingConfirmations();
    }

    async function refreshDialogueConfirmationSurface() {
      await Promise.allSettled([refreshDialogueTurns(), refreshDesktopPendingConfirmations()]);
      await validateDialogueContext({ announce: true });
      renderDialogueContextBar();
    }

    async function refreshSharedChatSurface() {
      const chatPage = $("#chatPage");
      if (
        chatHistoryRefreshInFlight ||
        document.hidden ||
        !(chatPage instanceof HTMLElement) ||
        chatPage.hidden
      ) {
        return;
      }
      chatHistoryRefreshInFlight = true;
      try {
        await refreshDialogueConfirmationSurface();
      } finally {
        chatHistoryRefreshInFlight = false;
      }
    }

    function startSharedChatSurfaceSync() {
      if (chatHistoryRefreshTimer !== null) return;
      chatHistoryRefreshTimer = window.setInterval(() => {
        void refreshSharedChatSurface();
      }, CHAT_HISTORY_REFRESH_INTERVAL_MS);
    }

    function updateDesktopDialogueTurn(turn) {
      const index = state.chat.findIndex((entry) => entry?.turn?.turn_id === turn?.turn_id);
      const entry = { turn };
      if (index >= 0) state.chat[index] = entry;
      else state.chat.push(entry);
      renderChat();
    }

    async function handleDesktopCardAction(button) {
      const card = button.closest(".dialogue-card");
      const turnId = card?.dataset.dialogueTurnId || "";
      const action = button.dataset.cardAction || "";
      const turn = state.chat.find((entry) => entry?.turn?.turn_id === turnId)?.turn;
      if (!turn || !action || button.disabled) return;
      button.disabled = true;
      try {
        const { response } = await executeCardAction(turn, action, {
          request(path, body) {
            return requestJsonStrict(path, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body)
            });
          },
          fetchTurn: fetchDesktopDialogueTurn,
          signal: dialogueCardActionAbortController.signal,
          onUpdate: updateDesktopDialogueTurn
        });
        if (response?.outcome === "retryable_error") {
          const reason = String(response?.reason || "").toLowerCase();
          if (reason === "stale_anchor" || reason === "anchor_dependency_failed") {
            showToast("这条暂时结算不了：你正在聊另一条，先把那条聊完或结束再试");
          } else {
            showToast("后端结果暂未同步；可刷新确认，或直接重试这次操作");
          }
          return;
        }
        if (action === "discuss") {
          await selectDialogueContext(turnId, response?.context_preview || null);
        } else if (dialogueContextSelection?.reply_to_turn_id === turnId) {
          storeDialogueContext(clearContextSelection());
          renderDialogueContextBar();
        }
        if (response?.outcome === "already_settled") showToast("这条已在另一个窗口结算，已同步最终状态");
        else if (action === "discuss") {
          showToast("好，沿着这条猜测继续聊");
          $("#chatInput")?.focus();
        } else if (action === "defer") showToast("先放一放，之后再聊");
        else if (response?.state === "revised") showToast("已按你的修正记下这条");
        else showToast(action === "confirm" ? "已确认这条猜测" : "已记下这条猜测不准");
        await refreshDialogueConfirmationSurface();
      } catch (error) {
        showToast(contextErrorMessage(error));
      }
    }

    async function handleDesktopPendingOpen(button) {
      const ref = button.dataset.confirmationRef || "";
      if (!ref || button.disabled) return;
      button.disabled = true;
      button.textContent = "打开中…";
      try {
        const turn = await executePendingConfirmationOpen(ref, {
          session: SHARED_CHAT_SESSION,
          signal: dialogueCardActionAbortController.signal,
          request(path, body, { signal } = {}) {
            return requestJsonStrict(path, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
              signal
            });
          },
          onWaiting({ message }) {
            button.textContent = "等待中…";
            showToast(`${message}，空闲后会自动打开`, { duration: 4200 });
          }
        });
        if (turn?.turn_id) {
          updateDesktopDialogueTurn(turn);
          await selectDialogueContext(turn.turn_id);
        }
        await refreshDialogueConfirmationSurface();
        showToast(isQuestionTurn(turn) ? "这条疑惑已经放进对话里" : "这张确认卡已经放进对话里");
        $("#chatInput")?.focus();
      } catch (error) {
        button.disabled = false;
        button.textContent = "打开";
        if (Number(error?.status) === 409) {
          await refreshDialogueConfirmationSurface();
          showToast("另一条疑惑正在聊，待聊列表已经同步");
        } else if (error?.name !== "AbortError") {
          const detail = String(error?.details?.detail?.message || "").trim();
          showToast(detail || "这条待聊内容暂时打不开，请稍后重试");
        }
      }
    }

    function renderChat({ forceBottom = false } = {}) {
      renderDialogueContextBar();
      renderDesktopPendingConfirmations();
      const chatLog = $("#chatLog");
      renderChatLogElement(chatLog, chatHtml(state.chat), { forceBottom });
      const messageChatLog = $("#messageChatLog");
      if (messageChatLog) {
        const baseMessages = state.messageChatPrompt
          ? state.chat.filter((msg) => msg.text !== "你可以直接告诉我最近想多看什么、少看什么，或者评价一条推荐为什么准/不准。")
          : state.chat;
        const messages = state.messageChatPrompt ? [{ role: "user", text: state.messageChatPrompt }, ...baseMessages] : baseMessages;
        renderChatLogElement(messageChatLog, chatHtml(messages), { forceBottom });
      }
    }

    // Same backoff shape as the card-action helper, driven by the chat path.
    // Only runs while an unsettled card is actually on screen. The budget
    // matches CARD_ACTION_POLL_DEADLINE_MS (~30s) rather than a few seconds:
    // an anchored settlement lands *after* the reply — the worker still has to
    // run attribution and the queue job — and an 8s budget measurably missed it
    // in browser E2E, leaving the card stuck on 正在聊这条 until a manual reload.
    const DIALOGUE_CARD_TERMINAL_STATES = new Set([
      "confirmed",
      "rejected",
      "revised",
      "deferred",
    ]);

    function hasUnsettledDialogueCard() {
      return state.chat.some((entry) => {
        const payload = entry?.turn?.payload;
        if (!payload || payload.type !== "card") return false;
        return !DIALOGUE_CARD_TERMINAL_STATES.has(String(payload.state || "").toLowerCase());
      });
    }

    async function refreshUntilDialogueCardsSettle() {
      if (!hasUnsettledDialogueCard()) return;
      for (const delay of [1000, 2000, 5000, 5000, 5000, 5000, 5000]) {
        await new Promise((resolve) => window.setTimeout(resolve, delay));
        await refreshDialogueTurns().catch(() => {});
        if (!hasUnsettledDialogueCard()) return;
      }
    }

    async function sendChat(message, options = {}) {
      const payloadMessage = options.contextPrefix ? `${options.contextPrefix}\n\n${message}` : message;
      const replyToTurnId = dialogueContextSelection?.["reply_to_turn_id"] || "";
      state.chat.push({ role: "user", text: message });
      state.chat.push({
        role: "agent",
        text: "新闻爪正在思考，等待模型回复…",
        thinking: true,
      });
      renderChat({ forceBottom: true });
      const payload = {
        session: SHARED_CHAT_SESSION,
        scope: options.scope || "chat",
        subject_id: options.subjectId || "",
        subject_title: options.subjectTitle || "",
        reply_to_turn_id: replyToTurnId,
        message: payloadMessage
      };
      let turn;
      try {
        turn = await requestJsonStrict(ENDPOINTS.chatTurns, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      } catch (error) {
        retainedChatDraft = message;
        const input = $("#chatInput");
        if (input) input.value = message;
        state.chat[state.chat.length - 1] = { role: "agent", text: contextErrorMessage(error) };
        renderChat();
        showToast(contextErrorMessage(error));
        return;
      }
      if (!turn?.turn_id) {
        state.chat[state.chat.length - 1] = { role: "agent", text: "当前没有连上后端，聊天没有提交成功。请检查 FastAPI 地址后重试。" };
        renderChat();
        showToast("聊天提交失败：后端不可用");
        return;
      }
      await refreshDialogueTurns().catch(() => {});
      void refreshDesktopPendingConfirmations().catch(() => {});
      const startedAt = Date.now();
      const poll = async () => {
        const latest = await requestJson(`${ENDPOINTS.chatTurns}/${encodeURIComponent(turn.turn_id)}`);
        if (latest?.status === "failed" || Date.now() - startedAt > 180000) {
          if (latest?.status === "failed") await refreshDialogueTurns().catch(() => {});
          else {
            state.chat.push({ role: "agent", text: "聊天处理超时，稍后可以在历史里继续查看。" });
            renderChat();
          }
          return;
        }
        if (latest?.status === "completed" || latest?.reply) {
          await refreshDialogueConfirmationSurface();
          // 回复完成 ≠ 结算完成：锚归属（support/contradict/revise/answer）是在回复
          // 之后由结算 worker 落库的，所以此刻卡片往往还停在 discussing。不补这一步，
          // 用户说完「我认可修正版」后卡片会一直显示「正在聊这条」，直到手动刷新。
          await refreshUntilDialogueCardsSettle();
          return;
        }
        window.setTimeout(poll, 1200);
      };
      window.setTimeout(poll, 1200);
    }

    async function refreshRecommendations() {
      const result = await requestJson(ENDPOINTS.refresh, { method: "POST" });
      if (result) {
        showToast("已请求后端开始补货");
        // 用户手动点的刷新，是明确要求换掉当前列表。
        await hydrateFromBackend({ replaceRecommendations: true });
      } else {
        showToast("刷新失败：请检查后端连接");
      }
    }

    function recommendationMutationOutcome(payload) {
      const status = String(payload?.status || "").trim().toLowerCase();
      return {
        status: ["ok", "exhausted", "degraded", "error"].includes(status) ? status : "ok",
        reason: String(payload?.reason || "").trim(),
        retryable: payload?.retryable !== false,
      };
    }

    function presentRecommendationMutationFailure(action, outcome) {
      const summary = $("#queueResultSummary");
      const reason = outcome.reason || `${action}暂时失败，当前队列已保留`;
      if (summary) {
        summary.dataset.tone = "error";
        summary.textContent = reason;
      }
      showToast(`${reason}${outcome.retryable ? "，可以稍后重试" : ""}`);
    }

    async function reshuffle() {
      const reshuffleButton = $("#reshuffleBtn");
      if (desktopRecommendationLoadState === "loading" || desktopRecommendationLoadState === "idle") {
        showToast("推荐队列还在读取，请稍候再换一批。");
        return;
      }
      if (reshuffleInFlight || appendMoreInFlight) {
        showToast("推荐队列正在更新，请等这次完成。");
        return;
      }
      if (recommendationLocalFiltersActive()) {
        const summary = $("#queueResultSummary");
        if (summary) {
          summary.dataset.tone = "error";
          summary.textContent = "请先重置本地筛选，再换一批";
        }
        showToast("主题、媒体和搜索只筛选当前列表；请先点「重置筛选」。");
        return;
      }
      reshuffleInFlight = true;
      // 请求发出前捕获当时选中的平台：用户在请求期间切 Tab 不能把响应写进错误批次。
      const requestPlatform = activePlatformSlug();
      // 当前可见卡片始终是本次换一批的排除集；平台定向时覆盖该平台
      // 本会话已加载的全部内容（不止可见的那些）。
      const visibleForExclusion = filteredVideos().filter((item) => item?.id != null);
      const visibleKeys = new Set(visibleForExclusion.map((item) => recommendationKey(item)));
      const scopedForExclusion = requestPlatform
        ? state.videos.filter((item) => item?.id != null && recommendationPlatformSlug(item) === requestPlatform)
        : visibleForExclusion;
      if (reshuffleButton) reshuffleButton.disabled = true;
      try {
        const excludedBvids = scopedForExclusion.map((item) => item.bvid).filter(Boolean);
        const requestBody = { excluded_bvids: excludedBvids };
        // "全部" 不带 source_platform：旧客户端 / 兼容路径的请求形状保持不变。
        if (requestPlatform) requestBody.source_platform = requestPlatform;
        const payload = await requestJsonStrict(ENDPOINTS.reshuffle, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(requestBody)
        });
        const outcome = recommendationMutationOutcome(payload);
        const returned = payload?.items?.length ? normalizeRecommendationList(payload.items) : [];
        if (requestPlatform && reportPlatformScopeLeak("换一批", requestPlatform, returned)) return;
        const fresh = returned.filter((item) => !visibleKeys.has(recommendationKey(item)));
        // 后端返回空数组时保留现有卡片，不制造空屏。
        if ((outcome.status === "error" || outcome.status === "degraded") && !fresh.length) {
          presentRecommendationMutationFailure("换一批", outcome);
        } else if (fresh.length && recommendationLocalFiltersActive()) {
          showToast("你在换批期间启用了本地筛选；新批次未覆盖当前队列。");
        } else if (fresh.length) {
          state.videos = requestPlatform ? replacePlatformCards(state.videos, requestPlatform, fresh) : fresh;
          renderAll();
          showToast("已换一批推荐");
        } else {
          showToast(outcome.status === "exhausted" ? "当前候选已看完，后台会继续补充" : "暂时没有更多新推荐了");
        }
      } catch (error) {
        const summary = $("#queueResultSummary");
        if (summary) {
          summary.dataset.tone = "error";
          summary.textContent = "换一批失败，当前队列已保留";
        }
        showToast(configErrorMessage(error?.details) || error?.message || "换一批失败，请检查后端连接");
      } finally {
        schedulePlatformAvailabilityRefresh();
        reshuffleInFlight = false;
        if (reshuffleButton) reshuffleButton.disabled = false;
        syncReshuffleFilterGuard();
      }
    }

    // 手动「加载更多」与滚动自动续页共用这一条路径。库存为 0 时按钮仍可点：
    // 它负责唤醒后端已有的补货链路；只有自动续页会被库存 gate 拦下。
    async function appendMore() {
      if (desktopRecommendationLoadState === "loading" || desktopRecommendationLoadState === "idle") {
        showToast("推荐队列还在读取，请稍候再加载。");
        return;
      }
      if (appendMoreInFlight || reshuffleInFlight) {
        showToast("推荐队列正在更新，请等这次完成。");
        return;
      }
      appendMoreInFlight = true;
      const loadMoreButton = $("#loadMoreBtn");
      if (loadMoreButton) loadMoreButton.disabled = true;
      // 与换一批同理：捕获请求开始时的平台，响应到达时不再读 state.filter。
      const requestPlatform = activePlatformSlug();
      showAppendSkeletons();
      try {
        const requestBody = { excluded_bvids: state.videos.map((v) => v.bvid) };
        if (requestPlatform) requestBody.source_platform = requestPlatform;
        const payload = await requestJsonStrict(ENDPOINTS.append, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(requestBody) });
        const outcome = recommendationMutationOutcome(payload);
        const retryHint = state.autoLoadOnScroll ? "补上后再次滚到底即可加载" : "稍后可再点一次";
        if ((outcome.status === "error" || outcome.status === "degraded") && !payload?.items?.length) {
          presentRecommendationMutationFailure("加载更多", outcome);
        } else if (payload?.items?.length) {
          const returned = normalizeRecommendationList(payload.items);
          if (requestPlatform && reportPlatformScopeLeak("加载更多", requestPlatform, returned)) return;
          // 按稳定 recommendation key 去重后再追加。
          const loadedKeys = new Set(state.videos.map((item) => recommendationKey(item)));
          const freshItems = returned.filter((item) => {
            const key = recommendationKey(item);
            if (!key || loadedKeys.has(key)) return false;
            loadedKeys.add(key);
            return true;
          });
          const appendCameUpShort = freshItems.length < APPEND_BATCH_SIZE;
          state.videos = state.videos.concat(freshItems);
          renderAll();
          // Keep decoding off the interaction path: slow first-miss covers should
          // not delay the new recommendation cards from appearing.
          void warmCoverImages(freshItems, { waitForDecode: true }).catch(() => {});
          if (!appendCameUpShort) {
            showToast("已加载更多推荐");
          } else if (freshItems.length) {
            showToast(`已加载 ${freshItems.length} 条，候选池暂时见底，后台正在补货，${retryHint}`);
          } else {
            showToast(`这批内容都已反馈过，后台正在补货，${retryHint}`);
          }
        } else {
          showToast(outcome.status === "exhausted"
            ? "当前候选已全部加载，后台会继续补充"
            : `候选池暂时没有新内容，已请求后台补货，${retryHint}`);
        }
      } catch (error) {
        const summary = $("#queueResultSummary");
        if (summary) {
          summary.dataset.tone = "error";
          summary.textContent = "加载失败，已有队列已保留";
        }
        showToast(configErrorMessage(error?.details) || error?.message || "加载更多失败，请检查后端连接");
      } finally {
        removeAppendSkeletons();
        // showAppendSkeletons may have cleared an empty-state placeholder; if
        // nothing came back, re-render so the grid never ends up blank.
        if (!grid.childElementCount) renderVideos();
        schedulePlatformAvailabilityRefresh();
        appendMoreInFlight = false;
        if (loadMoreButton) loadMoreButton.disabled = false;
      }
    }

    function normalizeRuntimeStatus(status) {
      if (!status) return null;
      const previous = state.runtimeStatus || {};
      const incomingType = String(status.type || status.runtime_event_type || "");
      const merged = { ...previous, ...status };
      let manualRefreshState = status.manual_refresh_state != null
        ? String(status.manual_refresh_state || "idle")
        : String(previous.manual_refresh_state || "");
      if (status.manual_refresh_state == null) {
        if (incomingType === "refresh.started" || incomingType === "refresh.strategy") manualRefreshState = "running";
        if (incomingType === "refresh.pool_updated") manualRefreshState = "success";
        if (incomingType === "refresh.failed") manualRefreshState = "failed";
      }
      return {
        initialized: merged.initialized !== false,
        recommendation_count: Number(merged.recommendation_count ?? 0),
        pending_signal_events: Number(merged.pending_signal_events ?? 0),
        last_refresh_at: String(merged.last_refresh_at ?? ""),
        last_notification_at: String(merged.last_notification_at ?? ""),
        unread_count: Number(merged.unread_count ?? state.messages.length ?? 0),
        pool_available_count: Number(merged.pool_available_count ?? merged.pool_available ?? merged.available_count ?? 0),
        pool_pending_count: Number(merged.pool_pending_count ?? 0),
        pool_target_count: Number(merged.pool_target_count ?? state.config?.scheduler?.pool_target_count ?? 0),
        last_discovered_count: Number(merged.last_discovered_count ?? 0),
        last_replenished_count: Number(merged.last_replenished_count ?? 0),
        recent_pool_topics: Array.isArray(merged.recent_pool_topics) ? merged.recent_pool_topics.map(String).filter(Boolean) : [],
        manual_refresh_state: manualRefreshState || "idle",
        manual_refresh_message: String(merged.manual_refresh_message || ""),
        runtime_event_type: incomingType || String(merged.runtime_event_type || ""),
        last_account_sync_at: String(merged.last_account_sync_at ?? ""),
        last_account_sync_error: String(merged.last_account_sync_error ?? ""),
        last_account_sync_error_kind: String(merged.last_account_sync_error_kind ?? ""),
        last_account_sync_issues: Array.isArray(merged.last_account_sync_issues)
          ? merged.last_account_sync_issues
          : [],
        // This is an explicit-key whitelist: a field missing here is dropped
        // silently, which is how the backend copy stopped reaching the chip.
        last_account_sync_message: String(merged.last_account_sync_message ?? ""),
        last_account_sync_severity: String(merged.last_account_sync_severity ?? ""),
        live_summary: String(merged.live_summary || merged.message || merged.state || "")
      };
    }

    function hasPostInitRuntimeSignals(runtime) {
      return Boolean(runtime) && (
        runtime.recommendation_count > 0 ||
        runtime.pool_available_count > 0 ||
        runtime.pool_pending_count > 0 ||
        runtime.last_replenished_count > 0 ||
        runtime.last_discovered_count > 0
      );
    }

    function shouldShowInitOnboarding(status) {
      const runtime = normalizeRuntimeStatus(status);
      if (Boolean(state.initStatus?.running)) return true;
      // init-status owns the terminal contract: normal completion already
      // verified a serviceable canonical row, while partial completion
      // explicitly lets the user enter and leaves replenishment to runtime.
      // A stale runtime snapshot must not recreate the onboarding card.
      if (state.initStatus?.initialized === true) return false;
      // /api/init-status is the authoritative pre-init source. runtime-status
      // can be transiently unreachable (state.runtimeStatus stays null) or get
      // rebuilt from field-less runtime events / message merges, where the
      // missing `initialized` defaults to true — neither may hide the guided
      // init card while the backend explicitly reports it never initialized.
      if (state.initStatus?.initialized === false && !state.videos.length && !hasPostInitRuntimeSignals(runtime)) {
        return true;
      }
      return Boolean(status) && runtime.initialized === false && !hasPostInitRuntimeSignals(runtime);
    }

    function getPoolStatusSummary(status) {
      const runtime = normalizeRuntimeStatus(status);
      if (!runtime || !runtime.initialized) return null;
      const sufficient = runtime.pool_target_count > 0 && runtime.pool_available_count >= runtime.pool_target_count;
      if (runtime.manual_refresh_state === "running") {
        return runtime.pool_available_count > 0
          ? { available: `还有 ${runtime.pool_available_count} 条可换`, replenished: "后台继续在找更多", topics: "可以先换一批，新的随时进" }
          : { available: "暂无可换库存", replenished: "正在补货", topics: "后台还在继续给你找新的" };
      }
      return {
        available: `还有 ${runtime.pool_available_count} 条可换`,
        replenished: runtime.last_replenished_count > 0
          ? `刚补进 ${runtime.last_replenished_count} 条`
          : runtime.last_discovered_count > 0
            ? "这轮找到了内容"
            : runtime.pool_pending_count > 0
              ? `另有 ${runtime.pool_pending_count} 条素材`
            : sufficient
              ? "这会儿先不补货"
              : "这轮还没补进",
        topics: runtime.recent_pool_topics.length > 0
          ? runtime.recent_pool_topics.join(" / ")
          : runtime.last_discovered_count > 0
            ? "但可立即换的库存还没变"
            : runtime.pool_pending_count > 0
              ? "素材已抓到，会按可换库存缺口整理"
            : sufficient
              ? "先把这一池给你慢慢换开"
              : "还在继续摸你的口味"
      };
    }

    function configuredSourceCount() {
      const sources = state.config?.sources;
      if (!sources || typeof sources !== "object") return 0;
      const shares = state.config?.scheduler?.pool_source_shares || {};
      return Object.entries(sources).reduce((count, [key, value]) => {
        if (!value || typeof value !== "object" || Array.isArray(value)) return count;
        if (Object.prototype.hasOwnProperty.call(value, "enabled")) {
          return count + (value.enabled !== false ? 1 : 0);
        }
        if (Object.prototype.hasOwnProperty.call(shares, key)) {
          return count + (Number(shares[key] ?? 0) > 0 ? 1 : 0);
        }
        return count;
      }, 0);
    }

    function syncSourceMetric() {
      const mediaCount = recommendationMediaCounts().size;
      const count = mediaCount || (state.videos.length > 0 ? 1 : configuredSourceCount());
      $("#metricSources").textContent = count ? String(count) : "—";
    }

    function getPoolRefreshLabel(runtime) {
      if (!runtime) return "—";
      if (runtime.manual_refresh_message) return runtime.manual_refresh_message;
      if (runtime.manual_refresh_state === "running") return runtime.pool_available_count > 0 ? "后台继续补货中" : "正在补货";
      if (runtime.manual_refresh_state === "success") return "刚同步完成";
      if (runtime.manual_refresh_state === "failed") return "刷新失败";
      if (runtime.pending_signal_events > 0) return `已记下 ${runtime.pending_signal_events} 个新动作`;
      if (runtime.runtime_event_type === "refresh.pool_updated") return "刚同步推荐池";
      return runtime.pool_available_count > 0 ? "可直接换一批" : "等待后台补货";
    }

    function setSideHealthState(kind, label = "") {
      const panel = document.querySelector(".side-health-summary");
      const target = $("#sideHealthState");
      const normalized = ["online", "degraded", "offline", "uninitialized", "checking"].includes(kind)
        ? kind
        : "checking";
      const fallback = {
        online: "在线",
        degraded: "降级",
        offline: "离线",
        uninitialized: "未初始化",
        checking: "连接中",
      }[normalized];
      if (panel) panel.dataset.health = normalized;
      if (target) target.textContent = label || fallback;
    }

    function renderPoolStatus(status = state.runtimeStatus) {
      const runtime = normalizeRuntimeStatus(status);
      const summary = getPoolStatusSummary(runtime);
      $("#poolAvailable").textContent = summary?.available || "后端未初始化";
      $("#poolReplenished").textContent = summary?.replenished || "—";
      $("#poolTopics").textContent = summary?.topics || "—";
      $("#poolRefreshState").textContent = getPoolRefreshLabel(runtime);
      renderDesktopRuntimeFailure();
    }

    function renderAccountSyncStatus(runtime) {
      const el = document.getElementById("accountSyncStatus");
      if (!el) return;
      const kind = String(runtime?.last_account_sync_error_kind || "");
      const error = String(runtime?.last_account_sync_error || "");
      const issues = Array.isArray(runtime?.last_account_sync_issues)
        ? runtime.last_account_sync_issues
        : [];
      const severity = String(runtime?.last_account_sync_severity || "");
      const sourceIssues = collectEnabledSourceIssues(state.sourceStatus);
      // Healthy installs (no sync or source issue) show nothing — zero visual
      // change. Pending verification alone is not an error and stays on the
      // source card instead of turning the dashboard into an alarm panel.
      if (!error && !kind && !issues.length && !sourceIssues.length) {
        el.hidden = true;
        el.textContent = "";
        el.classList.remove("is-auth-expired", "is-warning", "is-error");
        return;
      }
      el.hidden = false;
      // The backend renders the sentence so every surface says the same thing;
      // the literals here are only a fallback for an older backend.
      const message = String(runtime?.last_account_sync_message || "");
      const when = formatLocalTime(String(runtime?.last_account_sync_at || ""));
      const accountDetail = message || (error || kind || issues.length
        ? "账号同步遇到未分类异常，暂时无法确定具体环节"
        : "");
      const accountText = accountDetail && when
        ? `${accountDetail}（上次同步 ${when}）`
        : accountDetail;
      const sourceText = sourceIssues.length
        ? `来源接入：${sourceIssues.map((issue) => `${issue.source}：${issue.detail}`).join("；")}`
        : "";
      const combined = [accountText, sourceText].filter(Boolean).join("；");
      const hasSourceDanger = sourceIssues.some((issue) => issue.tone === "danger");
      if (kind === "auth_expired" && !hasSourceDanger) {
        el.classList.add("is-auth-expired", "is-warning");
        el.classList.remove("is-error");
        const fallback = "B 站登录已失效，账号同步已停止 — 请重新登录";
        el.textContent = [message ? accountText : fallback, sourceText].filter(Boolean).join("；");
        return;
      }
      const hasAccountIssue = Boolean(error || kind || issues.length);
      const warningOnly = !hasSourceDanger && (!hasAccountIssue || severity === "warning");
      el.classList.toggle("is-warning", warningOnly);
      el.classList.toggle("is-error", !warningOnly);
      el.classList.remove("is-auth-expired");
      el.textContent = combined;
    }

    function applyRuntimeStatus(payload) {
      if (!payload) return;
      state.runtimeStatus = normalizeRuntimeStatus(payload);
      const summary = getPoolStatusSummary(state.runtimeStatus);
      $("#statusLabel").textContent = state.runtimeStatus.initialized === false ? "后端未初始化" : "已连接本地后端";
      setSideHealthState(
        state.degraded ? "degraded" : state.runtimeStatus.initialized === false ? "uninitialized" : "online"
      );
      $("#metricPool").textContent = String(state.runtimeStatus.pool_available_count);
      syncMessageCount();
      syncSourceMetric();
      $("#runtimeSummary").textContent = state.runtimeStatus.live_summary || summary?.available || "后端在线，推荐池与采集运行时可读取。";
      renderPoolStatus(state.runtimeStatus);
      maybeAutoLoadAfterPoolRefill();
      renderAccountSyncStatus(state.runtimeStatus);
    }

    function setInput(id, value) {
      const el = document.getElementById(id);
      if (el && value !== undefined && value !== null) el.value = String(value);
    }

    function setCookieOverrideInput(id, currentCookie, platformLabel) {
      const el = document.getElementById(id);
      if (!el) return;
      el.value = "";
      const hasCookie = Boolean(String(currentCookie || "").trim());
      el.placeholder = hasCookie
        ? `已保存${platformLabel} Cookie；留空保存不会覆盖，需要更换时粘贴新的 Cookie`
        : `未保存${platformLabel} Cookie；需要手动覆盖时粘贴 Cookie`;
    }

    function getInput(id) {
      return document.getElementById(id)?.value?.trim() || "";
    }

    function getIntInput(id, fallback) {
      const value = Number.parseInt(getInput(id), 10);
      return Number.isFinite(value) ? value : fallback;
    }

    function getFloatInput(id, fallback) {
      const value = Number.parseFloat(getInput(id));
      return Number.isFinite(value) ? value : fallback;
    }

    const ZHIHU_SOURCE_MODE_FIELDS = [
      ["search", "zhihuModeSearch"],
      ["hot", "zhihuModeHot"],
      ["feed", "zhihuModeFeed"],
      ["creator", "zhihuModeCreator"],
      ["related", "zhihuModeRelated"],
    ];

    function setZhihuSourceModes(rawModes) {
      const fallbackModes = ZHIHU_SOURCE_MODE_FIELDS.map(([mode]) => mode);
      const selected = new Set(
        (Array.isArray(rawModes) && rawModes.length > 0 ? rawModes : fallbackModes)
          .map((mode) => String(mode).trim())
          .filter(Boolean),
      );
      for (const [mode, id] of ZHIHU_SOURCE_MODE_FIELDS) {
        const el = document.getElementById(id);
        if (el) el.checked = selected.has(mode);
      }
    }

    function collectZhihuSourceModes() {
      const selected = ZHIHU_SOURCE_MODE_FIELDS
        .filter(([, id]) => document.getElementById(id)?.checked === true)
        .map(([mode]) => mode);
      return selected.length > 0 ? selected : ["search"];
    }

    const REDDIT_SOURCE_MODE_FIELDS = [
      ["search", "redditModeSearch"],
      ["hot", "redditModeHot"],
      ["subreddit", "redditModeSubreddit"],
      ["related", "redditModeRelated"],
    ];

    function setRedditSourceModes(rawModes) {
      const fallbackModes = REDDIT_SOURCE_MODE_FIELDS.map(([mode]) => mode);
      const selected = new Set(
        (Array.isArray(rawModes) && rawModes.length > 0 ? rawModes : fallbackModes)
          .map((mode) => String(mode).trim())
          .filter(Boolean),
      );
      for (const [mode, id] of REDDIT_SOURCE_MODE_FIELDS) {
        const el = document.getElementById(id);
        if (el) el.checked = selected.has(mode);
      }
    }

    function collectRedditSourceModes() {
      const selected = REDDIT_SOURCE_MODE_FIELDS
        .filter(([, id]) => document.getElementById(id)?.checked === true)
        .map(([mode]) => mode);
      return selected.length > 0 ? selected : ["search"];
    }

    const BANGUMI_SOURCE_MODE_FIELDS = [
      ["search", "bangumiModeSearch"],
      ["ranked", "bangumiModeRanked"],
      ["latest", "bangumiModeLatest"],
    ];
    const LINUXDO_SOURCE_MODE_FIELDS = [
      ["search", "linuxdoModeSearch"],
      ["hot", "linuxdoModeHot"],
      ["feed", "linuxdoModeFeed"],
      ["creator", "linuxdoModeCreator"],
      ["related", "linuxdoModeRelated"],
    ];
    const WEIBO_SOURCE_MODE_FIELDS = [
      ["search", "weiboModeSearch"],
      ["hot", "weiboModeHot"],
      ["creator", "weiboModeCreator"],
    ];
    const BANGUMI_SUBJECT_TYPE_FIELDS = [
      ["anime", "bangumiTypeAnime"],
      ["book", "bangumiTypeBook"],
      ["game", "bangumiTypeGame"],
      ["music", "bangumiTypeMusic"],
      ["real", "bangumiTypeReal"],
    ];
    const V2EX_SOURCE_MODE_FIELDS = [
      ["search", "v2exModeSearch"],
      ["node", "v2exModeNode"],
      ["tab", "v2exModeTab"],
      ["hot", "v2exModeHot"],
      ["latest", "v2exModeLatest"],
    ];

    function setCheckedValues(fields, rawValues) {
      const fallback = fields.map(([value]) => value);
      const selected = new Set(
        (Array.isArray(rawValues) && rawValues.length > 0 ? rawValues : fallback)
          .map((value) => String(value).trim())
          .filter(Boolean),
      );
      fields.forEach(([value, id]) => {
        const el = document.getElementById(id);
        if (el) el.checked = selected.has(value);
      });
    }

    function collectCheckedValues(fields, fallback) {
      const selected = fields
        .filter(([, id]) => document.getElementById(id)?.checked === true)
        .map(([value]) => value);
      return selected.length > 0 ? selected : fallback;
    }

    function setWeiboSourceModes(rawValues) {
      const selected = Array.isArray(rawValues) ? [...rawValues] : rawValues;
      if (Array.isArray(selected) && selected.length === 1 && selected[0] === "creator") {
        selected.unshift("search");
      }
      setCheckedValues(WEIBO_SOURCE_MODE_FIELDS, selected);
    }

    function collectWeiboSourceModes() {
      const selected = collectCheckedValues(WEIBO_SOURCE_MODE_FIELDS, ["search"]);
      if (selected.length === 1 && selected[0] === "creator") {
        const search = document.getElementById("weiboModeSearch");
        if (search) search.checked = true;
        return ["search", "creator"];
      }
      return selected;
    }

    function joinPath(directory, filename) {
      const dir = String(directory || "").trim();
      const name = String(filename || "").trim();
      if (!dir) return name;
      if (!name) return dir;
      return dir.endsWith("/") || dir.endsWith("\\") ? `${dir}${name}` : `${dir}/${name}`;
    }

    function resolveLogPath(loggingConfig) {
      if (loggingConfig?.file_path) return loggingConfig.file_path;
      return joinPath(loggingConfig?.directory || "logs", loggingConfig?.filename || "newsclaw.log");
    }

    function splitLogPath(rawPath, currentLogging) {
      const fallback = { directory: "logs", filename: "newsclaw.log" };
      const trimmed = String(rawPath || "").trim();
      if (!trimmed) return fallback;
      if (currentLogging && trimmed === resolveLogPath(currentLogging)) {
        return { directory: currentLogging.directory || fallback.directory, filename: currentLogging.filename || fallback.filename };
      }
      const normalized = trimmed.replaceAll("\\", "/").replace(/\/+$/, "");
      const slashIndex = normalized.lastIndexOf("/");
      if (slashIndex === -1) return { directory: fallback.directory, filename: normalized || fallback.filename };
      return { directory: normalized.slice(0, slashIndex) || "/", filename: normalized.slice(slashIndex + 1) || fallback.filename };
    }

    function setSelect(id, value) {
      const el = document.getElementById(id);
      if (el && value !== undefined && value !== null) el.value = String(value);
    }

    // Unified per-source login / cookie status (GET /api/sources/status),
    // rendered with separate scheduling and credential/plugin states.
    //
    // The state -> label/tone table, the verify tones and the credential-row
    // shape all come from /shared/source-status.js, which the extension side
    // panel and the setup wizard load too. Keeping a private copy here is what
    // let the two surfaces drift into painting `no_auth` and `unverified` the
    // same colour (spec D6). The roster it exports includes Bangumi and V2EX;
    // Bangumi still uses the legacy `state` fallback, while V2EX has the
    // optional-PAT auth contract.
    const SourceStatus = globalThis.NewsClawSourceStatus;
    const SOURCE_STATUS_KEYS = SourceStatus.SOURCE_KEYS;
    const SOURCE_STATUS_REFRESH_EVENTS = new Set([
      "bilibili_cookie_synced",
      "douyin_cookie_synced",
      "x_cookie_synced",
      "reddit_cookie_synced"
    ]);
    // Extension wake-up signals are transport control frames, not user-facing
    // runtime activity. Rendering their wire type as the dashboard summary is
    // both noisy and misleading.
    const RUNTIME_TRANSPORT_ONLY_EVENTS = new Set(["dy_task_available"]);
    const SOURCE_ENABLE_SELECT_IDS = {
      bilibili: "bilibiliEnabled",
      xiaohongshu: "xhsEnabled",
      douyin: "douyinEnabled",
      weibo: "weiboEnabled",
      youtube: "youtubeEnabled",
      twitter: "twitterEnabled",
      zhihu: "zhihuEnabled",
      reddit: "redditEnabled",
      bangumi: "bangumiEnabled",
      linuxdo: "linuxdoEnabled",
      v2ex: "v2exEnabled"
    };

    function collectEnabledSourceIssues(data) {
      if (!data || typeof data !== "object") return [];
      return SOURCE_STATUS_KEYS.flatMap((key) => {
        const issue = SourceStatus.describeSourceIssue(data[key]);
        if (!issue) return [];
        return [{ ...issue, key, source: SourceStatus.sourceLabel(key) }];
      });
    }

    function setSourceBadge(badge, text, tone) {
      if (!badge) return;
      badge.textContent = text;
      badge.dataset.tone = tone;
    }

    // How strong the evidence behind the access verdict is, as its own badge
    // beside it. Two sources can honestly both read 已验证 while one asked the
    // platform and the other only found a file on disk; before this they were
    // the same green pill, which is the misreading the contract exists to fix.
    // Hidden rather than blanked when there is nothing to rate — a source that
    // needs no credential has no evidence, and an empty pill reads as a bug.
    function setSourceEvidence(badge, evidence) {
      if (!badge) return;
      const shown = Boolean(evidence && evidence.text);
      badge.hidden = !shown;
      badge.textContent = shown ? evidence.text : "";
      badge.dataset.rank = shown ? evidence.rank : "none";
      // The glyph and the method name already carry the distinction; the title
      // spells it out for anyone who wants it, and comes from the shared module
      // so it cannot drift from the glyph it explains.
      if (shown && evidence.hint) badge.title = evidence.hint;
      else badge.removeAttribute("title");
    }

    // The overseas-egress advisory is authored by the backend
    // (sources/platforms.py -> SourceStatusItem.network_hint) and rendered
    // verbatim. This function must never learn a platform name nor read
    // [network].mode: adding a platform must stay a one-line backend change.
    // Only the `enabled` gate lives here, because "is this row live right now"
    // is a UI fact the backend cannot see (the desktop select can be pending).
    function applySourceNetworkHint(row, hint, enabled) {
      const text = enabled ? String(hint || "") : "";
      let node = row.querySelector(".source-network-hint");
      if (!text) {
        if (node) node.remove();
        return;
      }
      if (!node) {
        node = document.createElement("p");
        node.className = "source-network-hint";
        // The card face owns a dedicated slot so the hint lands under the
        // status line instead of after the (collapsed) configuration body.
        (row.querySelector(".source-card-hint-slot") || row).appendChild(node);
      }
      node.textContent = text;
    }

    function getPendingSourceEnabled(key, item) {
      const select = document.getElementById(SOURCE_ENABLE_SELECT_IDS[key]);
      const currentEnabled = select ? select.value === "on" : Boolean(item?.enabled);
      const savedEnabled = typeof item?.enabled === "boolean" ? item.enabled : currentEnabled;
      return {
        currentEnabled,
        savedEnabled,
        pending: currentEnabled !== savedEnabled
      };
    }

    function renderSourcesStatusRows(data) {
      const list = $("#sourceStatusList");
      if (!list) return;
      SOURCE_STATUS_KEYS.forEach((key) => {
        const row = list.querySelector(`[data-source-status="${key}"]`);
        if (!row) return;
        const sourceBadge = row.querySelector(".source-source-badge");
        const accessBadge = row.querySelector(".source-access-badge");
        const evidenceBadge = row.querySelector(".source-evidence-badge");
        const detail = row.querySelector(".src-detail");
        const item = data?.[key];
        const access = SourceStatus.describeAccess(item);
        setSourceEvidence(evidenceBadge, access.evidence);
        if (!access.present) {
          setSourceBadge(sourceBadge, "来源：状态未知", "muted");
          setSourceBadge(accessBadge, `接入：${access.label}`, access.tone);
          if (detail) detail.textContent = access.detail;
          // No status means no basis for an egress advisory either; drop any
          // hint left over from the last successful poll.
          applySourceNetworkHint(row, "", false);
          row.classList.remove("source-row-unsaved");
          row.dataset.sourceEnabled = "unknown";
          row.dataset.accessTone = access.tone;
          return;
        }
        const enableState = getPendingSourceEnabled(key, item);
        const sourceLabel = enableState.pending
          ? `来源：${enableState.currentEnabled ? "将启用" : "将停用"}，保存后生效`
          : `来源：${enableState.savedEnabled ? "启用" : "停用"}`;
        setSourceBadge(sourceBadge, sourceLabel, enableState.pending ? "pending" : enableState.savedEnabled ? "enabled" : "disabled");
        setSourceBadge(accessBadge, `接入：${access.label}`, access.tone);
        const detailPrefix = enableState.pending ? "开关已改动，保存配置后才会进入/退出调度。 " : "";
        if (detail) detail.textContent = detailPrefix + (access.detail || "暂无更多状态细节。");
        applySourceNetworkHint(row, item.network_hint, enableState.currentEnabled);
        row.classList.toggle("source-row-unsaved", enableState.pending);
        row.dataset.sourceEnabled = enableState.currentEnabled ? "true" : "false";
        row.dataset.accessTone = access.tone;
      });
    }

    async function renderSourcesStatus() {
      let data = null;
      try { data = await requestJson("/sources/status"); } catch { data = null; }
      state.sourceStatus = data;
      renderSourcesStatusRows(data);
      renderAccountSyncStatus(state.runtimeStatus);
      await renderV2exIdentity();
    }

    const V2EX_IDENTITY_ORIGIN_LABELS = {
      pat: "PAT",
      browser: "浏览器",
      configured: "配置",
      accepted: "已选择"
    };

    function renderV2exIdentityResult(identity) {
      const statusEl = $("#v2exIdentityStatus");
      const acceptButton = $("#v2exAcceptBrowserIdentity");
      if (!statusEl || !acceptButton) return;
      const claims = identity?.claims && typeof identity.claims === "object" ? identity.claims : {};
      const browser = String(claims.browser || "").trim();
      const active = String(identity?.active_profile_identity?.username || "").trim();
      acceptButton.dataset.username = browser;
      acceptButton.hidden = !(browser && identity?.status === "identity_mismatch");
      if (!identity) {
        setProbeStatus(statusEl, "muted", "后端不可达，暂时无法读取身份状态。");
        return;
      }
      if (identity.status === "identity_mismatch") {
        const detail = Object.entries(claims)
          .map(([origin, username]) => `${V2EX_IDENTITY_ORIGIN_LABELS[origin] || origin}=${username}`)
          .join(" · ");
        setProbeStatus(statusEl, "error", `身份冲突：${detail}。账号初始化已暂停，公开发现仍可用。`);
        return;
      }
      if (identity.identity_switch_required) {
        setProbeStatus(
          statusEl,
          "warning",
          `当前浏览器账号 ${browser || identity.username}，画像仍属于 ${active}；增量同步已暂停，请运行一次 V2EX 完整初始化完成切换。`
        );
        return;
      }
      if (identity.status === "resolved") {
        const suffix = identity.private_bootstrap_available ? "，浏览器四 Scope 初始化可用。" : "；公开发现可用。";
        setProbeStatus(statusEl, "success", `当前账号 ${identity.username}${suffix}`);
        return;
      }
      setProbeStatus(statusEl, "muted", "尚未识别账号；匿名公开发现仍可用。");
    }

    async function renderV2exIdentity() {
      try {
        renderV2exIdentityResult(await requestJsonStrict("/sources/v2ex/identity", { timeoutMs: 12000 }));
      } catch {
        renderV2exIdentityResult(null);
      }
    }

    async function acceptCurrentV2exBrowserIdentity(button) {
      const username = String(button?.dataset?.username || "").trim();
      const statusEl = $("#v2exIdentityStatus");
      if (!username || button.disabled) return;
      button.disabled = true;
      setProbeStatus(statusEl, "pending", `正在采用浏览器账号 ${username}…`);
      try {
        await requestJsonStrict("/sources/v2ex/identity", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, accept: true }),
          timeoutMs: 12000
        });
        await renderV2exIdentity();
      } catch (error) {
        setProbeStatus(statusEl, "error", error?.message || "身份选择失败。");
      } finally {
        button.disabled = false;
      }
    }

    function renderVerifyResult(statusEl, result) {
      const view = SourceStatus.describeVerifyResult(result);
      setProbeStatus(statusEl, view.tone, view.text);
    }

    const sourceVerifyInFlight = new Set();

    async function runSourceVerify(row) {
      const slug = row?.dataset?.sourceStatus || "";
      if (!slug || sourceVerifyInFlight.has(slug)) return;
      const button = row.querySelector(".source-verify-btn");
      const statusEl = row.querySelector(".source-verify-status");
      sourceVerifyInFlight.add(slug);
      if (button) button.disabled = true;
      renderProbePending(statusEl, "连接");
      let cooldown = 0;
      try {
        const result = await requestJsonStrict(`/sources/${encodeURIComponent(slug)}/verify`, {
          method: "POST",
          timeoutMs: 30000
        });
        renderVerifyResult(statusEl, result);
        cooldown = Number(result?.retry_after_seconds) || 0;
        // Only a verification that actually moved the credential or the verdict
        // makes the badge above it stale; a refreshed timestamp does not.
        if (result?.changed) void renderSourcesStatus();
      } catch (error) {
        const view = SourceStatus.describeVerifyError(error);
        setProbeStatus(statusEl, view.tone, view.text);
      } finally {
        sourceVerifyInFlight.delete(slug);
        SourceStatus.startVerifyCooldown(button, cooldown);
      }
    }

    $("#sourceStatusList")?.addEventListener("click", (event) => {
      const identityButton = event.target.closest("#v2exAcceptBrowserIdentity");
      if (identityButton) {
        void acceptCurrentV2exBrowserIdentity(identityButton);
        return;
      }
      if (event.target.closest("#v2exRefreshIdentity")) {
        void renderV2exIdentity();
        return;
      }
      const button = event.target.closest(".source-verify-btn");
      if (!button || button.disabled) return;
      void runSourceVerify(button.closest(".source-status-row"));
    });

    function renderSourceCredentialRows(data) {
      // Status and credentials now live on the same per-source card, so both
      // renderers resolve their rows from one container.
      const list = $("#sourceStatusList");
      if (!list) return;
      SOURCE_STATUS_KEYS.forEach((key) => {
        const row = list.querySelector(`[data-source-credential="${key}"]`);
        if (!row) return;
        const summary = row.querySelector(".source-credential-summary");
        const value = row.querySelector(".source-credential-value");
        // Summary wording is the backend's, including 小红书's "a stored
        // content token is not a login" caveat. That caveat used to be a
        // per-platform branch right here, so only this page ever showed it —
        // the side panel and the setup wizard silently disagreed.
        const view = SourceStatus.describeCredential(data?.[key]);
        row.dataset.available = view.available ? "true" : "false";
        row.dataset.formKind = view.form.kind;
        if (summary) summary.textContent = view.summary;
        if (value) {
          value.value = view.value;
          // The masked box used to sit inside a collapsed <details>; on the card
          // it is always in view, so an empty preview — or one that just repeats
          // the summary line above it — is noise rather than information.
          const redundant = !view.value.trim() || view.value.trim() === view.summary.trim();
          value.hidden = redundant;
        }
      });
      // Not a display branch over the access enum: every other paste box gets
      // its "已保存/未保存" placeholder from the config snapshot in
      // populateForm(), but Reddit's credential goes to rdt-cli's own store, so
      // config.toml has no field to read and the hint has to come from here.
      // Generalising it would mean either a new contract field or moving three
      // other platforms off the config snapshot — both beyond this change.
      setCookieOverrideInput("redditCookie", data?.reddit?.available ? "synced" : "", " Reddit");
    }

    async function renderSourceCredentials() {
      let data = null;
      try { data = await requestJson(ENDPOINTS.sourceCredentials); } catch { data = null; }
      state.sourceCredentials = data;
      renderSourceCredentialRows(data);
    }

    // ---- 新闻源卡片：展开/折叠、停用态、占比双向同步 ----------------------
    const SOURCE_SHARE_INPUT_IDS = {
      bilibili: "shareBilibili",
      xiaohongshu: "shareXhs",
      douyin: "shareDouyin",
      youtube: "shareYoutube",
      twitter: "shareTwitter",
      zhihu: "shareZhihu",
      reddit: "shareReddit",
      bangumi: "shareBangumi",
      linuxdo: "shareLinuxdo",
      v2ex: "shareV2EX",
      weibo: "shareWeibo"
    };
    const SOURCE_CARD_LABELS = {
      bilibili: "Bilibili",
      xiaohongshu: "小红书",
      douyin: "抖音",
      weibo: "微博",
      youtube: "YouTube",
      twitter: "X (Twitter)",
      zhihu: "知乎",
      reddit: "Reddit",
      bangumi: "Bangumi",
      linuxdo: "Linux.do",
      v2ex: "V2EX"
    };
    const SOURCE_CARD_INLINE_COLORS = { linuxdo: "#1f6f43" };

    function sourceCardFor(key) {
      return $("#sourceStatusList")?.querySelector(`[data-source-status="${key}"]`) || null;
    }

    function setSourceCardOpen(card, open) {
      if (!card) return;
      card.dataset.open = open ? "1" : "0";
      card.querySelector(".source-card-face")?.setAttribute("aria-expanded", open ? "true" : "false");
    }

    // A card whose source is switched off keeps its inputs in the DOM (the save
    // payload still reads them) but stops advertising them as actionable.
    function syncSourceCardEnabledState() {
      SOURCE_STATUS_KEYS.forEach((key) => {
        const card = sourceCardFor(key);
        if (!card) return;
        const select = document.getElementById(SOURCE_ENABLE_SELECT_IDS[key]);
        const on = select ? select.value === "on" : true;
        card.dataset.sourceOff = on ? "false" : "true";
        if (!on) setSourceCardOpen(card, false);
      });
    }

    function renderShareOverview() {
      const bar = $("#shareOverviewBar");
      const legend = $("#shareOverviewLegend");
      if (!bar || !legend) return;
      const entries = SOURCE_STATUS_KEYS.map((key) => {
        const input = document.getElementById(SOURCE_SHARE_INPUT_IDS[key]);
        const select = document.getElementById(SOURCE_ENABLE_SELECT_IDS[key]);
        return {
          key,
          label: SOURCE_CARD_LABELS[key] || key,
          weight: Math.max(0, Number(input?.value) || 0),
          enabled: select ? select.value === "on" : true
        };
      });
      const active = entries.filter((item) => item.enabled && item.weight > 0);
      const total = active.reduce((sum, item) => sum + item.weight, 0);
      bar.textContent = "";
      active.forEach((item) => {
        const seg = document.createElement("i");
        seg.dataset.sourceKey = item.key;
        seg.style.width = `${(item.weight / total * 100).toFixed(2)}%`;
        if (SOURCE_CARD_INLINE_COLORS[item.key]) seg.style.background = SOURCE_CARD_INLINE_COLORS[item.key];
        seg.title = `${item.label}：${item.weight} 份`;
        bar.appendChild(seg);
      });
      bar.dataset.empty = active.length ? "false" : "true";
      legend.textContent = "";
      entries.forEach((item) => {
        const cell = document.createElement("span");
        cell.className = "share-overview-cell";
        cell.dataset.sourceKey = item.key;
        cell.dataset.off = item.enabled ? "false" : "true";
        const swatch = document.createElement("em");
        swatch.dataset.sourceKey = item.key;
        if (SOURCE_CARD_INLINE_COLORS[item.key]) swatch.style.background = SOURCE_CARD_INLINE_COLORS[item.key];
        const name = document.createElement("span");
        name.textContent = item.label;
        const value = document.createElement("b");
        value.textContent = item.enabled
          ? total > 0 && item.weight > 0 ? `${item.weight} · ${(item.weight / total * 100).toFixed(0)}%` : `${item.weight}`
          : "停用";
        cell.append(swatch, name, value);
        legend.appendChild(cell);
      });
    }

    function initSourceCards() {
      const list = $("#sourceStatusList");
      if (!list) return;

      list.addEventListener("click", (event) => {
        // Verify buttons keep their own handler; everything interactive inside
        // the body must not toggle the card.
        if (event.target.closest(".source-card-body, .source-card-right, .source-verify-btn")) return;
        const face = event.target.closest(".source-card-face");
        const card = face?.closest(".source-card");
        if (!card || card.dataset.sourceOff === "true") return;
        setSourceCardOpen(card, card.dataset.open !== "1");
      });

      list.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        const face = event.target.closest(".source-card-face");
        if (!face || event.target !== face) return;
        event.preventDefault();
        const card = face.closest(".source-card");
        if (!card || card.dataset.sourceOff === "true") return;
        setSourceCardOpen(card, card.dataset.open !== "1");
      });

      list.addEventListener("change", (event) => {
        if (event.target.matches(".source-card-enable select")) {
          syncSourceCardEnabledState();
          renderShareOverview();
          renderSourcesStatusRows(state.sourceStatus);
        }
      });

      list.addEventListener("input", (event) => {
        if (event.target.closest(".source-card-share")) renderShareOverview();
      });

      syncSourceCardEnabledState();
      renderShareOverview();
    }
    initSourceCards();

    // Login happens outside this page (user signs into a platform in another
    // tab). Runtime events make the normal update immediate; this visible-page
    // poll catches missed events / reconnect gaps and also keeps the dashboard
    // warning current when the source settings list is closed.
    setInterval(() => {
      if (document.hidden) return;
      void renderSourcesStatus();
      void refreshTrackedObservations({ silent: true });
    }, 30000);

    // LAN password-gate control. The web UI is served from 127.0.0.1, so it is a
    // trusted-local client (same-origin loopback) and may manage /api/auth/admin,
    // exactly like the extension's popup-auth-control.
    let lanAuthControl = null;
    let bootAutostartControl = null;

    function initLanAuthControl() {
      const checkbox = $("#authEnabled");
      const password = $("#authPassword");
      const passwordField = $("#authPasswordField");
      const saveRow = $("#authSaveRow");
      const saveBtn = $("#authSave");
      const hint = $("#authHint");
      if (!checkbox) return { reload: async () => {} };
      let current = null;
      const setHint = (msg) => { if (hint) hint.textContent = msg; };
      function syncEditing() {
        const can = Boolean(current && current.can_manage);
        const enabling = checkbox.checked;
        if (passwordField) passwordField.hidden = !(can && enabling);
        if (saveRow) saveRow.hidden = !(can && enabling);
      }
      function applyServerState() {
        const can = Boolean(current && current.can_manage);
        checkbox.checked = Boolean(current && current.enabled);
        checkbox.disabled = !can;
        syncEditing();
        if (!current) setHint("无法读取后端鉴权状态。");
        else if (!can) setHint(current.env_managed ? "由环境变量管理，请改环境变量并重启后端。" : "仅本机 / 浏览器插件可修改此设置。");
        else if (current.enabled) setHint("已开启：局域网 / 远程设备访问需要登录密码（本机与插件免登录）。");
        else setHint("已关闭：局域网访问无需密码。");
      }
      async function load() {
        current = await requestJson("/auth/status");
        applyServerState();
        return current;
      }
      async function apply(enabled) {
        const pwd = password ? String(password.value || "") : "";
        if (enabled && !pwd.trim()) { setHint("请输入要设置的访问密码。"); if (password?.focus) password.focus(); return; }
        setHint("保存中…");
        try {
          const payload = enabled ? { enabled: true, password: pwd } : { enabled: false };
          const result = await requestJsonStrict("/auth/admin", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
          if (result && result.ok === false) { setHint("保存失败，请重试。"); await load(); return; }
          if (password) password.value = "";
          await load();
        } catch (err) {
          const status = err?.status;
          if (status === 403) setHint("仅本机 / 插件可修改此设置。");
          else if (status === 409) setHint("由环境变量管理，无法在此修改。");
          else if (status === 400) setHint("开启密码门禁需要先设置密码。");
          else setHint("无法连接后端或保存失败，请重试。");
          await load();
        }
      }
      checkbox.addEventListener("change", () => {
        if (!checkbox.checked) void apply(false);
        else { syncEditing(); if (password?.focus) password.focus(); }
      });
      saveBtn?.addEventListener("click", () => void apply(true));
      void load();
      return { reload: load };
    }

    // Boot autostart control — mirrors the extension's popup-autostart-control.
    function initBootAutostartControl() {
      const checkbox = $("#autostartEnabled");
      const hint = $("#autostartHint");
      if (!checkbox) return { reload: async () => {} };
      let current = null;
      let busy = false;
      const setHint = (msg) => { if (hint) hint.textContent = msg; };
      function disabledHint(status) {
        const reason = status?.reason || "";
        if (reason === "env_managed") return "检测到环境变量配置，登录会话可能拿不到这些值；请先写入 config.toml。";
        if (reason === "shadowed") return "config.local.toml 正在覆盖开关，无法在此修改。";
        if (reason === "unsupported_docker_runtime") return "当前在 Docker / 容器环境中，不能注册桌面登录自启动。";
        if (reason === "unsupported_platform") return "当前平台暂不支持开机自启动。";
        if (reason === "local_only") return "仅本机 / 浏览器插件可修改此设置。";
        return "当前环境不能在这里修改开机自启动。";
      }
      function enabledHint(status) {
        const ollama = status?.manage_ollama ? "；本机 Ollama 配置会在需要时顺带拉起" : "";
        if (status?.registered === false) return `配置已开启，但系统注册缺失；下次后端启动会尝试修复${ollama}。`;
        return `已开启：下次登录系统会拉起后端，不启停当前进程${ollama}。`;
      }
      function activeHint(status) {
        if (!status) return "无法读取开机自启动状态。";
        if (!status.can_manage) return disabledHint(status);
        if (!status.enabled && status.registered) return "检测到系统自启动残留项；关闭此开关即可清理，当前后端进程不受影响。";
        if (status.enabled) return enabledHint(status);
        return "已关闭：不会注册登录自启动；当前后端进程不受影响。";
      }
      function applyServerState() {
        const can = Boolean(current && current.can_manage);
        checkbox.checked = Boolean(current && (current.enabled || current.registered));
        checkbox.disabled = busy || !can;
        setHint(activeHint(current));
      }
      async function load() {
        current = await requestJson("/autostart-status");
        applyServerState();
        return current;
      }
      async function apply(enabled) {
        busy = true;
        checkbox.disabled = true;
        setHint(enabled ? "正在开启开机自启动…" : "正在关闭开机自启动…");
        try {
          const result = await requestJsonStrict("/autostart/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: Boolean(enabled) }) });
          current = result || current;
          busy = false;
          applyServerState();
          await load();
        } catch (err) {
          busy = false;
          const status = err?.status;
          current = err?.details || current;
          if (status === 403) setHint("仅本机 / 浏览器插件可修改此设置。");
          else if (status === 409) setHint(disabledHint(current));
          else setHint("无法连接后端或保存失败，请重试。");
          await load();
        }
      }
      checkbox.addEventListener("change", () => void apply(Boolean(checkbox.checked)));
      void load();
      return { reload: load };
    }

    const LLM_PROVIDER_LABELS = {
      openai: "OpenAI",
      claude: "Claude",
      gemini: "Gemini",
      deepseek: "DeepSeek",
      openrouter: "OpenRouter",
      ollama: "Ollama",
      openai_compatible: "OpenAI-compatible"
    };
    const LLM_PROVIDER_DEFAULTS = {
      openai: { model: "gpt-5-nano", base_url: "" },
      claude: { model: "claude-sonnet-4-20250514", base_url: "" },
      gemini: { model: "gemini-2.5-flash", base_url: "" },
      deepseek: { model: "deepseek-v4-flash", base_url: "https://api.deepseek.com" },
      openrouter: { model: "openai/gpt-4o-mini", base_url: "https://openrouter.ai/api/v1" },
      ollama: { model: "qwen2.5:7b", base_url: "http://127.0.0.1:11434/v1" },
      openai_compatible: { model: "", base_url: "" }
    };
    const LLM_MODEL_DISCOVERY_PROVIDERS = new Set([
      "openai",
      "deepseek",
      "openrouter",
      "ollama",
      "openai_compatible"
    ]);
    const LLM_MODULE_UI = {
      soul: { mode: "moduleSoulMode", custom: "moduleSoulCustom", list: "moduleSoulChain", picker: "moduleSoulPicker", add: "moduleSoulAdd", label: "画像理解" },
      discovery: { mode: "moduleDiscoveryMode", custom: "moduleDiscoveryCustom", list: "moduleDiscoveryChain", picker: "moduleDiscoveryPicker", add: "moduleDiscoveryAdd", label: "内容发现" },
      recommendation: { mode: "moduleRecommendationMode", custom: "moduleRecommendationCustom", list: "moduleRecommendationChain", picker: "moduleRecommendationPicker", add: "moduleRecommendationAdd", label: "推荐表达" },
      evaluation: { mode: "moduleEvaluationMode", custom: "moduleEvaluationCustom", list: "moduleEvaluationChain", picker: "moduleEvaluationPicker", add: "moduleEvaluationAdd", label: "内容评估" }
    };
    const LLM_INSTANCE_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;
    let llmDialogReturnFocus = null;
    let llmDraggedRoute = null;

    function clonePlain(value) {
      return JSON.parse(JSON.stringify(value ?? null));
    }

    function normalizeLlmDraft(llm) {
      const instances = {};
      for (const [rawId, rawInstance] of Object.entries(llm?.instances || {})) {
        const id = String(rawId || "").trim().toLowerCase();
        if (!id || !rawInstance || typeof rawInstance !== "object") continue;
        instances[id] = {
          name: String(rawInstance.name || id),
          provider_type: String(rawInstance.provider_type || ""),
          enabled: rawInstance.enabled !== false,
          api_key: String(rawInstance.api_key || ""),
          model: String(rawInstance.model || ""),
          base_url: String(rawInstance.base_url || ""),
          auth_mode: String(rawInstance.auth_mode || ""),
          api_flavor: String(rawInstance.api_flavor || ""),
          http_referer: String(rawInstance.http_referer || ""),
          x_title: String(rawInstance.x_title || ""),
          reasoning_effort: String(rawInstance.reasoning_effort || ""),
          num_ctx: Number.parseInt(rawInstance.num_ctx, 10) || 0
        };
      }
      const defaultChain = Array.from(new Set((llm?.default_chain || []).map((item) => String(item || "").trim().toLowerCase()).filter(Boolean)));
      const routes = {};
      for (const moduleName of Object.keys(LLM_MODULE_UI)) {
        const rawRoute = llm?.routes?.[moduleName] || llm?.[moduleName] || {};
        routes[moduleName] = {
          inherit: rawRoute.inherit !== false,
          chain: Array.from(new Set((rawRoute.chain || []).map((item) => String(item || "").trim().toLowerCase()).filter(Boolean)))
        };
      }
      return { instances, default_chain: defaultChain, routes };
    }

    function llmInstanceReferences(instanceId) {
      const draft = state.llmDraft;
      if (!draft) return [];
      const references = [];
      if (draft.default_chain.includes(instanceId)) references.push("默认链");
      for (const [moduleName, ui] of Object.entries(LLM_MODULE_UI)) {
        const route = draft.routes[moduleName];
        if (route && !route.inherit && route.chain.includes(instanceId)) references.push(ui.label);
      }
      return references;
    }

    function llmEndpointSummary(instance) {
      const raw = String(instance?.base_url || "").trim();
      if (!raw) return "官方默认地址";
      try {
        const url = new URL(raw);
        return `${url.host}${url.pathname === "/" ? "" : url.pathname}`;
      } catch {
        return raw;
      }
    }

    function llmActionIcon(action) {
      if (action === "up") return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 15 6-6 6 6"/></svg>';
      if (action === "down") return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
      return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>';
    }

    function llmRouteChain(scope) {
      if (!state.llmDraft) return [];
      return scope === "default"
        ? state.llmDraft.default_chain
        : state.llmDraft.routes[scope]?.chain || [];
    }

    function setLlmRouteChain(scope, chain) {
      if (!state.llmDraft) return;
      if (scope === "default") state.llmDraft.default_chain = chain;
      else state.llmDraft.routes[scope].chain = chain;
    }

    function renderLlmChain(scope, listId, pickerId) {
      const list = document.getElementById(listId);
      const picker = document.getElementById(pickerId);
      if (!list || !picker || !state.llmDraft) return;
      const chain = llmRouteChain(scope);
      const instances = state.llmDraft.instances;
      if (!chain.length) {
        list.innerHTML = '<li class="llm-empty-state">还没有实例。请从下方加入一个端点。</li>';
      } else {
        list.innerHTML = chain.map((instanceId, index) => {
          const instance = instances[instanceId];
          const name = instance?.name || instanceId;
          const detail = instance
            ? `${LLM_PROVIDER_LABELS[instance.provider_type] || instance.provider_type} · ${instance.model || "未填写模型"}`
            : "实例不存在";
          const removeDisabled = scope === "default" && chain.length <= 1;
          return `<li class="llm-chain-item" draggable="true" data-route-scope="${escapeHtml(scope)}" data-instance-id="${escapeHtml(instanceId)}">
            <span class="llm-chain-position" aria-label="优先级 ${index + 1}">${index + 1}</span>
            <span class="llm-chain-copy"><strong>${escapeHtml(name)}</strong><small>${escapeHtml(detail)}</small></span>
            <span class="llm-chain-actions">
              <button class="llm-chain-action" type="button" data-chain-action="up" aria-label="上移 ${escapeHtml(name)}" ${index === 0 ? "disabled" : ""}>${llmActionIcon("up")}</button>
              <button class="llm-chain-action" type="button" data-chain-action="down" aria-label="下移 ${escapeHtml(name)}" ${index === chain.length - 1 ? "disabled" : ""}>${llmActionIcon("down")}</button>
              <button class="llm-chain-action" type="button" data-chain-action="remove" aria-label="从调用链移除 ${escapeHtml(name)}" ${removeDisabled ? "disabled" : ""}>${llmActionIcon("remove")}</button>
            </span>
          </li>`;
        }).join("");
      }
      const candidates = Object.entries(instances).filter(([instanceId, instance]) => instance.enabled !== false && !chain.includes(instanceId));
      picker.innerHTML = candidates.length
        ? candidates.map(([instanceId, instance]) => `<option value="${escapeHtml(instanceId)}">${escapeHtml(instance.name || instanceId)} · ${escapeHtml(instance.model || "未填写模型")}</option>`).join("")
        : '<option value="">没有可添加的实例</option>';
      picker.disabled = candidates.length === 0;
      const addButton = scope === "default"
        ? $("#addLlmDefaultChainItem")
        : document.getElementById(LLM_MODULE_UI[scope]?.add || "");
      if (addButton) addButton.disabled = candidates.length === 0;

      list.querySelectorAll(".llm-chain-item").forEach((item) => {
        item.addEventListener("dragstart", (event) => {
          llmDraggedRoute = { scope, instanceId: item.dataset.instanceId };
          item.classList.add("is-dragging");
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", item.dataset.instanceId || "");
        });
        item.addEventListener("dragend", () => {
          llmDraggedRoute = null;
          list.querySelectorAll(".llm-chain-item").forEach((row) => row.classList.remove("is-dragging", "is-drop-target"));
        });
        item.addEventListener("dragover", (event) => {
          if (!llmDraggedRoute || llmDraggedRoute.scope !== scope) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
          item.classList.add("is-drop-target");
        });
        item.addEventListener("dragleave", () => item.classList.remove("is-drop-target"));
        item.addEventListener("drop", (event) => {
          event.preventDefault();
          const sourceId = llmDraggedRoute?.instanceId;
          const targetId = item.dataset.instanceId;
          if (!sourceId || !targetId || sourceId === targetId) return;
          const next = [...llmRouteChain(scope)];
          const sourceIndex = next.indexOf(sourceId);
          const targetIndex = next.indexOf(targetId);
          if (sourceIndex < 0 || targetIndex < 0) return;
          next.splice(sourceIndex, 1);
          next.splice(targetIndex, 0, sourceId);
          setLlmRouteChain(scope, next);
          renderLlmRouting();
          markSettingsDirty();
        });
      });
      list.querySelectorAll("[data-chain-action]").forEach((button) => {
        button.addEventListener("click", () => {
          const row = button.closest(".llm-chain-item");
          const instanceId = row?.dataset.instanceId || "";
          const action = button.dataset.chainAction;
          const next = [...llmRouteChain(scope)];
          const index = next.indexOf(instanceId);
          if (index < 0) return;
          if (action === "up" && index > 0) [next[index - 1], next[index]] = [next[index], next[index - 1]];
          if (action === "down" && index < next.length - 1) [next[index + 1], next[index]] = [next[index], next[index + 1]];
          if (action === "remove") next.splice(index, 1);
          setLlmRouteChain(scope, next);
          renderLlmRouting();
          markSettingsDirty();
        });
      });
    }

    function renderLlmInstances() {
      const container = $("#llmInstanceList");
      if (!container || !state.llmDraft) return;
      const entries = Object.entries(state.llmDraft.instances);
      if (!entries.length) {
        container.innerHTML = '<div class="llm-empty-state">尚未配置 LLM 实例。新建一个端点后即可组成调用链。</div>';
        return;
      }
      container.innerHTML = entries.map(([instanceId, instance]) => {
        const references = llmInstanceReferences(instanceId);
        const probe = state.llmProbeResults.get(instanceId);
        const probeText = probe?.pending
          ? "正在探测…"
          : probe
          ? formatProbeResult(probe)
          : "尚未测试";
        const probeTone = probe?.pending ? "pending" : probe?.ok === true ? "success" : probe ? "error" : "";
        return `<article class="llm-instance-card" data-enabled="${instance.enabled !== false}" data-instance-card="${escapeHtml(instanceId)}">
          <div class="llm-instance-card-head">
            <div class="llm-instance-card-title"><strong>${escapeHtml(instance.name || instanceId)}</strong><code>${escapeHtml(instanceId)}</code></div>
            <span class="llm-instance-badge" data-tone="${instance.enabled !== false ? "success" : "muted"}">${instance.enabled !== false ? "已启用" : "已停用"}</span>
          </div>
          <div class="llm-instance-badges"><span class="llm-instance-badge">${escapeHtml(LLM_PROVIDER_LABELS[instance.provider_type] || instance.provider_type)}</span>${references.map((reference) => `<span class="llm-instance-badge" data-tone="muted">${escapeHtml(reference)}</span>`).join("")}</div>
          <p class="llm-instance-meta"><span>模型：${escapeHtml(instance.model || "未填写")}</span><span>地址：${escapeHtml(llmEndpointSummary(instance))}</span></p>
          <p class="llm-instance-probe" data-tone="${probeTone}" aria-live="polite">${escapeHtml(probeText)}</p>
          <div class="llm-instance-actions">
            <button class="pill-btn" type="button" data-instance-action="probe" data-instance-id="${escapeHtml(instanceId)}">测试</button>
            <button class="pill-btn" type="button" data-instance-action="edit" data-instance-id="${escapeHtml(instanceId)}">编辑</button>
            <button class="pill-btn" type="button" data-instance-action="delete" data-instance-id="${escapeHtml(instanceId)}">删除</button>
          </div>
        </article>`;
      }).join("");
      container.querySelectorAll("[data-instance-action]").forEach((button) => {
        button.addEventListener("click", () => {
          const instanceId = button.dataset.instanceId || "";
          if (button.dataset.instanceAction === "probe") void runLlmInstanceProbe(instanceId);
          if (button.dataset.instanceAction === "edit") openLlmInstanceDialog(instanceId);
          if (button.dataset.instanceAction === "delete") deleteLlmInstance(instanceId);
        });
      });
    }

    function renderLlmRouting() {
      if (!state.llmDraft) return;
      renderLlmInstances();
      renderLlmChain("default", "llmDefaultChain", "llmDefaultChainPicker");
      for (const [moduleName, ui] of Object.entries(LLM_MODULE_UI)) {
        const route = state.llmDraft.routes[moduleName];
        setSelect(ui.mode, route.inherit ? "inherit" : "custom");
        const custom = document.getElementById(ui.custom);
        if (custom) custom.hidden = route.inherit;
        renderLlmChain(moduleName, ui.list, ui.picker);
      }
    }

    function addLlmChainItem(scope) {
      const picker = scope === "default"
        ? $("#llmDefaultChainPicker")
        : document.getElementById(LLM_MODULE_UI[scope]?.picker || "");
      const instanceId = String(picker?.value || "").trim();
      if (!instanceId) return;
      const chain = llmRouteChain(scope);
      if (!chain.includes(instanceId)) {
        setLlmRouteChain(scope, [...chain, instanceId]);
        markSettingsDirty();
      }
      renderLlmRouting();
    }

    function renderLlmDatalist(id, values, currentValue = "") {
      const list = document.getElementById(id);
      if (!(list instanceof HTMLDataListElement)) return;
      const normalized = [...new Set(
        [...(Array.isArray(values) ? values : []), currentValue]
          .map((value) => String(value || "").trim())
          .filter(Boolean)
      )];
      list.replaceChildren(...normalized.map((value) => {
        const option = document.createElement("option");
        option.value = value;
        return option;
      }));
    }

    function setLlmModelDiscoveryStatus(tone, text) {
      const status = $("#llmInstanceModelDiscoveryStatus");
      if (!status) return;
      status.dataset.tone = tone || "neutral";
      status.textContent = text;
    }

    function resetLlmModelDiscovery() {
      renderLlmDatalist("llmInstanceModelOptions", []);
      const providerType = getInput("llmInstanceProviderType");
      const supported = LLM_MODEL_DISCOVERY_PROVIDERS.has(providerType);
      const button = $("#refreshLlmInstanceModels");
      if (button) {
        button.hidden = !supported;
        button.disabled = false;
        button.textContent = "获取模型";
      }
      setLlmModelDiscoveryStatus(
        "neutral",
        supported
          ? "可从 OpenAI 兼容 /models 获取；接口不支持时仍可手填。"
          : "该 Provider 没有 OpenAI /models 发现契约，模型名请手填。"
      );
    }

    function buildLlmModelDiscoveryRequest() {
      if (!state.llmDraft) return null;
      const existingId = state.llmEditingInstanceId;
      const instanceId = String(
        existingId || getInput("llmInstanceId") || "model-discovery-draft"
      ).trim().toLowerCase();
      const current = state.llmDraft.instances[existingId] || {};
      const providerType = getInput("llmInstanceProviderType").trim();
      const typedKey = getInput("llmInstanceApiKey");
      const apiKey = $("#llmInstanceClearApiKey")?.checked
        ? ""
        : typedKey || current.api_key || "";
      const instance = {
        ...current,
        name: getInput("llmInstanceName").trim() || current.name || instanceId,
        provider_type: providerType,
        enabled: true,
        api_key: apiKey,
        model: getInput("llmInstanceModel").trim(),
        base_url: getInput("llmInstanceBaseUrl").trim(),
        auth_mode: providerType === "openai"
          ? getInput("llmInstanceAuthMode") || "api_key"
          : "",
        api_flavor: ["openai", "openai_compatible"].includes(providerType)
          ? getInput("llmInstanceApiFlavor")
          : "",
        http_referer: providerType === "openrouter"
          ? getInput("llmInstanceReferer").trim()
          : "",
        x_title: providerType === "openrouter"
          ? getInput("llmInstanceTitle").trim()
          : "",
        reasoning_effort: ["openai", "claude", "gemini", "deepseek", "openrouter", "openai_compatible"].includes(providerType)
          ? getInput("llmInstanceReasoning").trim()
          : "",
        num_ctx: providerType === "ollama"
          ? Math.max(0, getIntInput("llmInstanceNumCtx", 0))
          : 0
      };
      return {
        instanceId,
        config: {
          llm: {
            routing_version: 2,
            instances: {
              ...clonePlain(state.llmDraft.instances),
              [instanceId]: instance
            },
            default_chain: [...state.llmDraft.default_chain],
            routes: clonePlain(state.llmDraft.routes)
          }
        }
      };
    }

    async function discoverLlmInstanceModels() {
      const request = buildLlmModelDiscoveryRequest();
      const button = $("#refreshLlmInstanceModels");
      if (!request || !button || button.disabled) return;
      button.disabled = true;
      button.textContent = "获取中…";
      setLlmModelDiscoveryStatus("pending", "正在向当前端点请求 /models…");
      try {
        const result = await requestJsonStrict(ENDPOINTS.configModelDiscovery, {
          method: "POST",
          timeoutMs: 25000,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            instance_id: request.instanceId,
            config: request.config
          })
        });
        if (Array.isArray(result?.reasoning_efforts) && result.reasoning_efforts.length) {
          renderLlmDatalist(
            "llmInstanceReasoningOptions",
            result.reasoning_efforts,
            getInput("llmInstanceReasoning")
          );
        }
        if (!result?.ok) {
          throw new Error(result?.error || "端点没有返回模型列表");
        }
        const models = Array.isArray(result.models) ? result.models : [];
        renderLlmDatalist("llmInstanceModelOptions", models, getInput("llmInstanceModel"));
        setLlmModelDiscoveryStatus(
          "success",
          models.length
            ? `已获取 ${models.length} 个模型；可从下拉选择，也可继续手填。`
            : "接口返回了空列表；保留当前手填值。"
        );
      } catch (error) {
        setLlmModelDiscoveryStatus(
          "error",
          `获取失败：${error?.message || "未知错误"}；当前输入未改动，仍可手填。`
        );
      } finally {
        button.disabled = false;
        button.textContent = "获取模型";
      }
    }

    function syncLlmInstanceConditionalFields() {
      const providerType = getInput("llmInstanceProviderType");
      document.querySelectorAll("[data-instance-field]").forEach((field) => {
        const kind = field.dataset.instanceField;
        const visible =
          (kind === "openai-auth" && providerType === "openai") ||
          (kind === "openai-protocol" && ["openai", "openai_compatible"].includes(providerType)) ||
          (kind === "reasoning" && ["openai", "claude", "gemini", "deepseek", "openrouter", "openai_compatible"].includes(providerType)) ||
          (kind === "ollama" && providerType === "ollama") ||
          (kind === "openrouter" && providerType === "openrouter");
        field.hidden = !visible;
      });
      resetLlmModelDiscovery();
    }

    function openLlmInstanceDialog(instanceId = "") {
      if (!state.llmDraft) return;
      const dialog = $("#llmInstanceDialog");
      const instance = instanceId ? state.llmDraft.instances[instanceId] : null;
      state.llmEditingInstanceId = instanceId;
      llmDialogReturnFocus = document.activeElement;
      dialog.dataset.providerType = instance?.provider_type || "";
      $("#llmInstanceDialogTitle").textContent = instance ? "编辑 LLM 实例" : "新建 LLM 实例";
      setInput("llmInstanceName", instance?.name || "");
      setInput("llmInstanceId", instanceId);
      $("#llmInstanceId").disabled = Boolean(instance);
      setSelect("llmInstanceProviderType", instance?.provider_type || "openai");
      setSelect("llmInstanceEnabled", instance?.enabled === false ? "off" : "on");
      setInput("llmInstanceModel", instance?.model || "");
      setInput("llmInstanceBaseUrl", instance?.base_url || "");
      setInput("llmInstanceApiKey", "");
      $("#llmInstanceApiKey").placeholder = instance?.api_key ? "已配置；留空保留原密钥" : "输入 API Key";
      $("#llmInstanceClearApiKey").checked = false;
      $("#llmInstanceClearApiKeyField").hidden = !instance?.api_key;
      setSelect("llmInstanceAuthMode", instance?.auth_mode || "api_key");
      setSelect("llmInstanceApiFlavor", instance?.api_flavor || "");
      setInput("llmInstanceReasoning", instance?.reasoning_effort || "");
      setInput("llmInstanceNumCtx", instance?.num_ctx || 0);
      setInput("llmInstanceReferer", instance?.http_referer || "");
      setInput("llmInstanceTitle", instance?.x_title || "");
      $("#llmInstanceFormError").textContent = "";
      renderLlmDatalist(
        "llmInstanceReasoningOptions",
        ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
        instance?.reasoning_effort || ""
      );
      syncLlmInstanceConditionalFields();
      if (!instance) applyLlmProviderDefaults();
      dialog.hidden = false;
      document.body.classList.add("llm-dialog-open");
      window.setTimeout(() => (instance ? $("#llmInstanceName") : $("#llmInstanceName"))?.focus(), 0);
    }

    function closeLlmInstanceDialog() {
      const dialog = $("#llmInstanceDialog");
      if (!dialog || dialog.hidden) return;
      dialog.hidden = true;
      document.body.classList.remove("llm-dialog-open");
      state.llmEditingInstanceId = "";
      if (llmDialogReturnFocus?.focus) llmDialogReturnFocus.focus();
      llmDialogReturnFocus = null;
    }

    function saveLlmInstanceDraft() {
      if (!state.llmDraft) return;
      const existingId = state.llmEditingInstanceId;
      const instanceId = String(existingId || getInput("llmInstanceId")).trim().toLowerCase();
      const name = getInput("llmInstanceName").trim();
      const providerType = getInput("llmInstanceProviderType").trim();
      const enabled = getInput("llmInstanceEnabled") !== "off";
      const model = getInput("llmInstanceModel").trim();
      const baseUrl = getInput("llmInstanceBaseUrl").trim();
      const error = $("#llmInstanceFormError");
      if (!LLM_INSTANCE_ID_PATTERN.test(instanceId)) {
        error.textContent = "实例 ID 只能使用小写字母、数字、下划线和连字符，且最长 64 个字符。";
        $("#llmInstanceId")?.focus();
        return;
      }
      if (!existingId && state.llmDraft.instances[instanceId]) {
        error.textContent = "这个实例 ID 已经存在。";
        $("#llmInstanceId")?.focus();
        return;
      }
      if (!name) {
        error.textContent = "请填写实例名称。";
        $("#llmInstanceName")?.focus();
        return;
      }
      if (enabled && !model) {
        error.textContent = "启用的实例必须明确填写模型。";
        $("#llmInstanceModel")?.focus();
        return;
      }
      if (enabled && providerType === "openai_compatible" && !baseUrl) {
        error.textContent = "OpenAI-compatible 实例必须填写 Base URL。";
        $("#llmInstanceBaseUrl")?.focus();
        return;
      }
      const current = state.llmDraft.instances[existingId] || {};
      const typedKey = getInput("llmInstanceApiKey");
      const authMode = providerType === "openai" ? getInput("llmInstanceAuthMode") || "api_key" : "";
      const effectiveKey = $("#llmInstanceClearApiKey")?.checked
        ? ""
        : typedKey || current.api_key || "";
      if (enabled && !["ollama", "gemini"].includes(providerType) && !(providerType === "openai" && authMode === "codex_oauth") && !effectiveKey) {
        error.textContent = "启用的远端实例需要 API Key。";
        $("#llmInstanceApiKey")?.focus();
        return;
      }
      if (!enabled && llmInstanceReferences(existingId).length) {
        error.textContent = `请先从这些调用链移除实例：${llmInstanceReferences(existingId).join("、")}。`;
        return;
      }
      state.llmDraft.instances[instanceId] = {
        name,
        provider_type: providerType,
        enabled,
        api_key: effectiveKey,
        model,
        base_url: baseUrl,
        auth_mode: authMode,
        api_flavor: ["openai", "openai_compatible"].includes(providerType) ? getInput("llmInstanceApiFlavor") : "",
        http_referer: providerType === "openrouter" ? getInput("llmInstanceReferer").trim() : "",
        x_title: providerType === "openrouter" ? getInput("llmInstanceTitle").trim() : "",
        reasoning_effort: ["openai", "claude", "gemini", "deepseek", "openrouter", "openai_compatible"].includes(providerType) ? getInput("llmInstanceReasoning") : "",
        num_ctx: providerType === "ollama" ? Math.max(0, getIntInput("llmInstanceNumCtx", 0)) : 0
      };
      closeLlmInstanceDialog();
      renderLlmRouting();
      markSettingsDirty();
    }

    function deleteLlmInstance(instanceId) {
      if (!state.llmDraft?.instances[instanceId]) return;
      const references = llmInstanceReferences(instanceId);
      if (references.length) {
        showToast(`无法删除：仍被 ${references.join("、")} 引用`);
        return;
      }
      const name = state.llmDraft.instances[instanceId].name || instanceId;
      if (!window.confirm(`删除 LLM 实例「${name}」？`)) return;
      delete state.llmDraft.instances[instanceId];
      state.llmProbeResults.delete(instanceId);
      renderLlmRouting();
      markSettingsDirty();
    }

    function applyLlmProviderDefaults() {
      const providerType = getInput("llmInstanceProviderType");
      const defaults = LLM_PROVIDER_DEFAULTS[providerType] || {};
      const dialog = $("#llmInstanceDialog");
      const previousType = String(dialog?.dataset.providerType || "");
      const previousDefaults = LLM_PROVIDER_DEFAULTS[previousType] || {};
      const isNew = !state.llmEditingInstanceId;
      const model = getInput("llmInstanceModel");
      const baseUrl = getInput("llmInstanceBaseUrl");
      if (!model || (isNew && model === (previousDefaults.model || ""))) {
        setInput("llmInstanceModel", defaults.model || "");
      }
      if (!baseUrl || (isNew && baseUrl === (previousDefaults.base_url || ""))) {
        setInput("llmInstanceBaseUrl", defaults.base_url || "");
      }
      const previousBaseId = previousType.replace(/_/g, "-");
      const currentId = getInput("llmInstanceId");
      if (!currentId || (isNew && currentId === previousBaseId)) {
        let candidate = providerType.replace(/_/g, "-");
        let suffix = 2;
        while (state.llmDraft?.instances[candidate]) candidate = `${providerType.replace(/_/g, "-")}-${suffix++}`;
        setInput("llmInstanceId", candidate);
      }
      const previousLabel = LLM_PROVIDER_LABELS[previousType] || previousType;
      const currentName = getInput("llmInstanceName");
      if (!currentName || (isNew && currentName === previousLabel)) {
        setInput("llmInstanceName", LLM_PROVIDER_LABELS[providerType] || providerType);
      }
      if (dialog) dialog.dataset.providerType = providerType;
      syncLlmInstanceConditionalFields();
    }

    function applyConfig(config) {
      if (!config || typeof config !== "object") return;
      state.degraded = config.degraded === true;
      state.config = config;
      const scheduler = config.scheduler || {};
      setSelect("schedulerEnabled", scheduler.enabled === false ? "off" : "on");
      setSelect("pauseDisconnect", scheduler.pause_on_extension_disconnect === false ? "keep" : "pause");
      setInput("extensionDisconnectGrace", scheduler.extension_disconnect_grace_seconds);
      setInput("poolTarget", scheduler.pool_target_count);
      setInput("accountSyncInterval", scheduler.account_sync_interval_hours);
      setInput("refreshCheckInterval", scheduler.refresh_check_interval_seconds);
      setInput("signalEventThreshold", scheduler.signal_event_threshold);
      setInput("feedbackBatchThreshold", scheduler.feedback_batch_threshold);
      setInput("trendingRefreshMinutes", scheduler.trending_refresh_minutes);
      setInput("exploreRefreshMinutes", scheduler.explore_refresh_minutes);
      setInput("discoveryLimit", scheduler.discovery_limit);
      setInput("proactivePushInterval", scheduler.proactive_push_interval_seconds);
      setInput("speculatorIdleInterval", scheduler.speculator_idle_interval_minutes);
      setSelect("autoUpdate", scheduler.auto_update_enabled === true ? "on" : "off");
      setInput("autoUpdateInterval", scheduler.auto_update_check_interval_hours);
      setInput("speculationInterval", scheduler.speculation_interval_minutes);
      setInput("speculationTtl", scheduler.speculation_ttl_days);
      setInput("speculationCooldown", scheduler.speculation_cooldown_days);
      setInput("speculationThreshold", scheduler.speculation_confirmation_threshold);
      setInput("speculationMaxActive", scheduler.speculation_max_active);
      setInput("speculationMaxPrimary", scheduler.speculation_max_primary_interests);
      setInput("speculationMaxSecondary", scheduler.speculation_max_secondary_interests);

      setSelect("language", config.language || "zh");
      setInput("dataDir", config.data_dir);
      setInput("storageDbPath", "由数据目录自动管理（内部兼容路径）");
      // Mirrors the [network].mode backend default (system since v0.3.175);
      // only reached if /api/config omits the field.
      setSelect("networkProxyMode", config.network?.mode || "system");
      setInput("networkProxy", config.network?.proxy || "");
      const llm = config.llm || {};
      setInput("llmConcurrency", llm.concurrency ?? 4);
      setInput("llmTimeout", llm.timeout);
      state.llmDraft = normalizeLlmDraft(llm);
      state.llmProbeResults.clear();
      renderLlmRouting();
      setSelect("embeddingProvider", llm.embedding?.provider || "");
      const embeddingFallbackProvider = llm.embedding?.fallback_provider || "";
      setSelect("embeddingFallbackProvider", embeddingFallbackProvider);
      setInput("embeddingModel", llm.embedding?.model);
      setInput("embeddingApiKey", llm.embedding?.api_key);
      setInput("embeddingBaseUrl", llm.embedding?.base_url);
      setInput("embeddingOutputDimensionality", llm.embedding?.output_dimensionality ?? 1024);
      setInput("embeddingSimilarity", llm.embedding?.similarity_threshold);
      setSelect("biliAuth", config.bilibili?.auth_method || "cookie");
      setCookieOverrideInput("biliCookie", config.bilibili?.cookie, " B 站");
      setInput("biliBrowserExecutable", config.bilibili?.browser_executable);
      setSelect("biliBrowserHeaded", config.bilibili?.browser_headed === true ? "on" : "off");
      setSelect("bilibiliEnabled", config.sources?.bilibili?.enabled === false ? "off" : "on");
      setInput("bilibiliMinInterval", config.sources?.bilibili?.min_interval_minutes);
      setInput("sourcesBrowserCdp", config.sources?.browser?.cdp_url);
      setSelect("sourcesBrowserHeaded", config.sources?.browser?.headed === true ? "on" : "off");
      setSelect("xhsEnabled", config.sources?.xiaohongshu?.enabled === true ? "on" : "off");
      setInput("xhsDailySearchBudget", config.sources?.xiaohongshu?.daily_search_budget);
      setInput("xhsDailyCreatorBudget", config.sources?.xiaohongshu?.daily_creator_budget);
      setInput("xhsTaskInterval", config.sources?.xiaohongshu?.task_interval_seconds);
      setInput("xhsMinInterval", config.sources?.xiaohongshu?.min_interval_minutes);
      setSelect("douyinEnabled", config.sources?.douyin?.enabled === true ? "on" : "off");
      setCookieOverrideInput("douyinCookie", config.sources?.douyin?.cookie, "抖音");
      setInput("douyinCookieEnv", config.sources?.douyin?.cookie_env);
      setInput("douyinDailySearchBudget", config.sources?.douyin?.daily_search_budget);
      setInput("douyinDailyHotBudget", config.sources?.douyin?.daily_hot_budget);
      setInput("douyinDailyFeedBudget", config.sources?.douyin?.daily_feed_budget);
      setInput("douyinRequestInterval", config.sources?.douyin?.request_interval_seconds);
      setInput("douyinMinInterval", config.sources?.douyin?.min_interval_minutes);
      setSelect("weiboEnabled", config.sources?.weibo?.enabled === true ? "on" : "off");
      setWeiboSourceModes(config.sources?.weibo?.source_modes);
      setInput("weiboDailySearchBudget", config.sources?.weibo?.daily_search_budget);
      setInput("weiboDailyHotBudget", config.sources?.weibo?.daily_hot_budget);
      setInput("weiboDailyCreatorBudget", config.sources?.weibo?.daily_creator_budget);
      setInput("weiboRequestInterval", config.sources?.weibo?.request_interval_seconds);
      setInput("weiboMinInterval", config.sources?.weibo?.min_interval_minutes);
      setSelect("youtubeEnabled", config.sources?.youtube?.enabled === true ? "on" : "off");
      setInput("youtubeDailySearchBudget", config.sources?.youtube?.daily_search_budget);
      setInput("youtubeDailyTrendingBudget", config.sources?.youtube?.daily_trending_budget);
      setInput("youtubeDailyChannelBudget", config.sources?.youtube?.daily_channel_budget);
      setInput("youtubeRequestInterval", config.sources?.youtube?.request_interval_seconds);
      setInput("youtubeMinInterval", config.sources?.youtube?.min_interval_minutes);
      setSelect("twitterEnabled", config.sources?.twitter?.enabled === true ? "on" : "off");
      setCookieOverrideInput("twitterCookie", config.sources?.twitter?.cookie, " X");
      setInput("twitterCookieEnv", config.sources?.twitter?.cookie_env);
      setInput("twitterDailySearchBudget", config.sources?.twitter?.daily_search_budget);
      setInput("twitterDailyFeedBudget", config.sources?.twitter?.daily_feed_budget);
      setInput("twitterDailyCreatorBudget", config.sources?.twitter?.daily_creator_budget);
      setInput("twitterRequestInterval", config.sources?.twitter?.request_interval_seconds);
      setInput("twitterMinInterval", config.sources?.twitter?.min_interval_minutes);
      setSelect("zhihuEnabled", config.sources?.zhihu?.enabled === true ? "on" : "off");
      setZhihuSourceModes(config.sources?.zhihu?.source_modes);
      setInput("zhihuDailySearchBudget", config.sources?.zhihu?.daily_search_budget);
      setInput("zhihuDailyHotBudget", config.sources?.zhihu?.daily_hot_budget);
      setInput("zhihuDailyFeedBudget", config.sources?.zhihu?.daily_feed_budget);
      setInput("zhihuDailyCreatorBudget", config.sources?.zhihu?.daily_creator_budget);
      setInput("zhihuDailyRelatedBudget", config.sources?.zhihu?.daily_related_budget);
      setInput("zhihuRequestInterval", config.sources?.zhihu?.request_interval_seconds);
      setInput("zhihuMinInterval", config.sources?.zhihu?.min_interval_minutes);
      setSelect("redditEnabled", config.sources?.reddit?.enabled === true ? "on" : "off");
      setSelect("redditBackend", config.sources?.reddit?.backend || "rdt");
      setRedditSourceModes(config.sources?.reddit?.source_modes);
      setInput("redditDailySearchBudget", config.sources?.reddit?.daily_search_budget);
      setInput("redditDailyHotBudget", config.sources?.reddit?.daily_hot_budget);
      setInput("redditDailySubredditBudget", config.sources?.reddit?.daily_subreddit_budget);
      setInput("redditDailyRelatedBudget", config.sources?.reddit?.daily_related_budget);
      setInput("redditRequestInterval", config.sources?.reddit?.request_interval_seconds);
      setInput("redditMinInterval", config.sources?.reddit?.min_interval_minutes);
      setSelect("bangumiEnabled", config.sources?.bangumi?.enabled === true ? "on" : "off");
      setInput("bangumiUsername", config.sources?.bangumi?.username);
      {
        // The token itself is never returned by GET (secret); access_token_set
        // only tells us whether one is stored. Keep the field empty and signal
        // the stored state via placeholder so an untouched save never clobbers it.
        const bangumiToken = document.getElementById("bangumiAccessToken");
        if (bangumiToken) {
          bangumiToken.value = "";
          bangumiToken.placeholder = config.sources?.bangumi?.access_token_set
            ? "已配置（留空保持不变；填写新令牌以替换）"
            : "可留空；填写以自动识别当前用户并读取私密收藏";
        }
        // Clear-token is a per-save action; never leave it pre-checked after a
        // reload, and hide it when nothing is stored to clear.
        const bangumiClearToken = document.getElementById("bangumiClearToken");
        if (bangumiClearToken) {
          bangumiClearToken.checked = false;
          bangumiClearToken.disabled = !config.sources?.bangumi?.access_token_set;
        }
      }
      setCheckedValues(BANGUMI_SOURCE_MODE_FIELDS, config.sources?.bangumi?.source_modes);
      setCheckedValues(BANGUMI_SUBJECT_TYPE_FIELDS, config.sources?.bangumi?.subject_types);
      setInput("bangumiDailySearchBudget", config.sources?.bangumi?.daily_search_budget);
      setInput("bangumiDailyRankedBudget", config.sources?.bangumi?.daily_ranked_budget);
      setInput("bangumiDailyLatestBudget", config.sources?.bangumi?.daily_latest_budget);
      setInput("bangumiRequestInterval", config.sources?.bangumi?.request_interval_seconds);
      setInput("bangumiMinInterval", config.sources?.bangumi?.min_interval_minutes);
      setInput("bangumiBootstrapLimit", config.sources?.bangumi?.bootstrap_limit);
      setSelect("linuxdoEnabled", config.sources?.linuxdo?.enabled === true ? "on" : "off");
      setCheckedValues(LINUXDO_SOURCE_MODE_FIELDS, config.sources?.linuxdo?.source_modes);
      setInput("linuxdoDailySearchBudget", config.sources?.linuxdo?.daily_search_budget);
      setInput("linuxdoDailyHotBudget", config.sources?.linuxdo?.daily_hot_budget);
      setInput("linuxdoDailyFeedBudget", config.sources?.linuxdo?.daily_feed_budget);
      setInput("linuxdoDailyCreatorBudget", config.sources?.linuxdo?.daily_creator_budget);
      setInput("linuxdoDailyRelatedBudget", config.sources?.linuxdo?.daily_related_budget);
      setInput("linuxdoRequestInterval", config.sources?.linuxdo?.request_interval_seconds);
      setInput("linuxdoMinInterval", config.sources?.linuxdo?.min_interval_minutes);
      setInput("linuxdoBootstrapLimit", config.sources?.linuxdo?.bootstrap_limit);
      setSelect("v2exEnabled", config.sources?.v2ex?.enabled === true ? "on" : "off");
      setInput("v2exUsername", config.sources?.v2ex?.username);
      {
        const v2exToken = document.getElementById("v2exAccessToken");
        if (v2exToken) {
          v2exToken.value = "";
          v2exToken.placeholder = config.sources?.v2ex?.access_token_set
            ? "已配置（留空保持不变；填写新 PAT 以替换）"
            : "可留空；匿名公开发现可直接使用";
        }
        const v2exClearToken = document.getElementById("v2exClearToken");
        if (v2exClearToken) {
          v2exClearToken.checked = false;
          v2exClearToken.disabled = config.sources?.v2ex?.access_token_set !== true;
        }
      }
      setCheckedValues(V2EX_SOURCE_MODE_FIELDS, config.sources?.v2ex?.source_modes);
      setInput("v2exDailySearchBudget", config.sources?.v2ex?.daily_search_budget);
      setInput("v2exDailyNodeBudget", config.sources?.v2ex?.daily_node_budget);
      setInput("v2exDailyTabBudget", config.sources?.v2ex?.daily_tab_budget);
      setInput("v2exDailyHotBudget", config.sources?.v2ex?.daily_hot_budget);
      setInput("v2exDailyLatestBudget", config.sources?.v2ex?.daily_latest_budget);
      setInput("v2exRequestInterval", config.sources?.v2ex?.request_interval_seconds);
      setInput("v2exMinInterval", config.sources?.v2ex?.min_interval_minutes);
      const newsSources = config.sources?.news || {};
      const tushareSources = config.sources?.tushare || {};
      const rssSources = config.sources?.rss || {};
      const setNewsSource = (prefix, source, fallbackEnabled = true) => {
        setSelect(`${prefix}Enabled`, source?.enabled === false ? "off" : (fallbackEnabled ? "on" : "off"));
        setInput(`${prefix}Weight`, source?.weight);
        setInput(`${prefix}Interval`, source?.refresh_interval_minutes);
      };
      setNewsSource("newsTushareNews", newsSources.tushare_news || tushareSources.news);
      setNewsSource("newsTushareMajor", newsSources.tushare_major_news || tushareSources.major_news);
      setNewsSource("newsTushareCctv", newsSources.tushare_cctv_news || tushareSources.cctv_news);
      setNewsSource("newsOfficialRss", newsSources.official_rss || rssSources.official);
      setNewsSource("newsCustomRss", newsSources.custom_rss || rssSources.custom, false);
      const customUrls = newsSources.custom_rss?.urls || rssSources.custom?.urls;
      if (Array.isArray(customUrls)) setInput("newsCustomRssUrls", customUrls.join("\n"));
      if (!state.initBangumiUsernameTouched) {
        state.initBangumiUsername = config.sources?.bangumi?.username || "";
        // A successful prefill populated the field; a later explicit clear is
        // then a deliberate reset (sends ""), while an untouched or config-failed
        // empty field omits the username to keep the configured value.
        state.initBangumiUsernamePrefilled = true;
      }
      // The enable selects and share weights were just repopulated from the
      // snapshot, so the cards' collapsed/disabled state and the share bar have
      // to be recomputed from the new values rather than the pre-apply ones.
      if (!state.degraded) void refreshNewsSourceContracts();

      setSelect("logLevel", config.logging?.level || "INFO");
      setSelect("logFileLevel", config.logging?.file_level || "DEBUG");
      setInput("logPath", resolveLogPath(config.logging));
      setInput("logMaxFileSize", config.logging?.max_file_size_mb);
      setInput("logBackupCount", config.logging?.backup_count);
      setInput("logAggregateBudget", config.logging?.aggregate_budget_mb);
      setInput("logUnmanagedTruncate", config.logging?.unmanaged_truncate_mb);
      setInput("logUnmanagedMaxAge", config.logging?.unmanaged_max_age_days);

      if ($("#configStatus")) $("#configStatus").value = "配置已从后端加载。";
      if (state.runtimeStatus) applyRuntimeStatus(state.runtimeStatus);
      restoreFrontendSettings();
      // The form now mirrors the backend snapshot, so whatever was pending
      // before this apply is no longer pending.
      clearSettingsDirty();
    }

    function applyConfigSnapshot(snapshot) {
      const configSnapshot = snapshot?.config || snapshot;
      applyConfig(configSnapshot);
      presentDegradedConfigRecovery(configSnapshot);
      renderFilters();
      syncSourceMetric();
    }

    function normalizeDelight(item) {
      if (!item) return null;
      if (!isNewsDelightItem(item)) return null;
      const canonical = window.NewsClawSavedSync.normalizeSavedItem(item);
      const priority = normalizeRecommendationPriority(item);
      // 后端 pending-batch 对喜欢过的候选下发 state="liked"，重灌后恢复
      // 「已喜欢」文案，让用户看出这条已经表过态。
      const serverState = String(item.state ?? "");
      const fallbackMessage = serverState === "liked" ? "好，这类多来点。" : "";
      // Same defense as the grid (issue #79): the delight card was the exact
      // `<h3 id="delightTitle">answer_<id>` the report screenshotted. Route the
      // title through the ID fallback (derive from body / placeholder), but
      // keep the friendly delight default when there is genuinely no title.
      const delightBody = decodeHtmlEntities(item.body_text ?? "");
      const delightCt = canonical.content_type.toLowerCase();
      const derivedTitle = displayRecommendationTitle(
        decodeHtmlEntities(item.title ?? ""), delightBody, delightCt);
      return {
        type: "delight",
        id: item.recommendation_id ?? item.id ?? null,
        recommendation_id: item.recommendation_id ?? item.id ?? null,
        bvid: String(item.bvid ?? item.content_id ?? ""),
        item_key: canonical.item_key,
        content_id: canonical.content_id,
        content_type: canonical.content_type,
        title: derivedTitle && derivedTitle !== "未命名内容"
          ? derivedTitle
          : "发现了一条你可能会意外喜欢的内容",
        body_text: delightBody,
        reason: decodeHtmlEntities(item.delight_reason ?? item.reason ?? item.delight_hook ?? item.message ?? "这条来自后端高惊喜分候选。"),
        cover_url: normalizeImageUrl(item.cover_url ?? item.cover ?? item.pic ?? item.thumbnail_url ?? item.thumbnail ?? item.image_url),
        content_url: canonical.content_url || String(item.canonical_url || ""),
        source_platform: canonical.source_platform,
        platform: canonical.source_platform,
        chat_turn_id: String(item.chat_turn_id ?? ""),
        chat_reply: String(item.chat_reply ?? item.reply ?? ""),
        chat_draft: String(item.chat_draft ?? ""),
        state: serverState,
        response_message: String(item.response_message ?? "") || fallbackMessage,
        published_at: String(item?.published_at ?? "").trim(),
        published_label: String(item?.published_label ?? "").replace(/\s+/g, " ").trim().slice(0, 64),
        // Engagement stats so the delight card shows the same ▶/👍/💬 row as the
        // grid (v0.3.159+; 0 = not fetched → recommendationStats renders nothing).
        view_count: Number(item?.view_count ?? 0) || 0,
        like_count: Number(item?.like_count ?? 0) || 0,
        comment_count: Number(item?.comment_count ?? 0) || 0,
        share_count: Number(item?.share_count ?? 0) || 0,
        danmaku_count: Number(item?.danmaku_count ?? 0) || 0,
        favorite_count: Number(item?.favorite_count ?? 0) || 0,
        rating_score: Number(item?.rating_score ?? 0) || 0,
        rating_count: Number(item?.rating_count ?? 0) || 0,
        source_rank: Number(item?.source_rank ?? 0) || 0,
        topic: decodeHtmlEntities(item?.topic_label ?? item?.topic ?? "未归类"),
        topic_group: decodeHtmlEntities(item?.topic_group ?? "").replace(/\s+/g, " ").trim(),
        display_tags: normalizeDisplayTags(item?.display_tags),
        priority,
        priority_level: priority.level,
        priority_label: priority.label,
        priority_score: priority.score,
        priority_reason: priority.reason,
        priority_signals: priority.signals,
        why_recommended: decodeHtmlEntities(item?.why_recommended ?? item?.delight_reason ?? item?.reason ?? ""),
        impact_chain: decodeHtmlEntities(item?.impact_chain ?? ""),
        observation_points: (Array.isArray(item?.observation_points)
          ? item.observation_points
          : String(item?.observation_points || "").trim()
            ? [item.observation_points]
            : [])
          .map((point) => decodeHtmlEntities(point).replace(/\s+/g, " ").trim())
          .filter(Boolean),
        expression: decodeHtmlEntities(item?.expression ?? item?.delight_reason ?? item?.reason ?? ""),
        turns: delightTurnList(item.turns)
      };
    }

    function renderDelightTextMedia(thumb, delight) {
      if (!thumb || !delight) return;
      const bodyText = String(delight.body_text || "").trim();
      if (!bodyText) return;
      thumb.replaceChildren();
      thumb.classList.remove("has-image", "is-tag-media");
      thumb.classList.add("is-text-media");
      thumb.dataset.platform = String(delight.source_platform || "bilibili").toLowerCase();
      const text = document.createElement("p");
      text.className = "delight-text-media-copy";
      text.textContent = bodyText;
      const badge = document.createElement("span");
      badge.className = "platform";
      badge.textContent = platformName(delight.source_platform);
      badge.dataset.platform = String(delight.source_platform || "bilibili").toLowerCase();
      thumb.append(text, badge);
    }

    function renderDelightTagMedia(thumb, delight) {
      if (!thumb || !delight) return;
      thumb.replaceChildren();
      thumb.classList.remove("has-image", "is-text-media");
      thumb.classList.add("is-tag-media");
      delete thumb.dataset.platform;
      thumb.innerHTML = recommendationTagFingerprintHtml(delight);
    }

    function renderDelightFallbackMedia(thumb, delight) {
      if (recommendationPlatformSlug(delight) === "news") {
        renderDelightTagMedia(thumb, delight);
        return;
      }
      const bodyText = String(delight?.body_text || "").trim();
      if (bodyText) {
        renderDelightTextMedia(thumb, delight);
        return;
      }
      thumb.replaceChildren();
      thumb.classList.remove("has-image", "is-text-media", "is-tag-media");
      delete thumb.dataset.platform;
      if (!delight) return;
      const badge = document.createElement("span");
      badge.className = "platform";
      badge.textContent = platformName(delight.source_platform);
      badge.dataset.platform = String(delight.source_platform || "bilibili").toLowerCase();
      thumb.append(badge);
    }

    function renderDelightCover(delight) {
      const thumb = syncDelightThumbLink(delight);
      if (!thumb) return;
      const url = imageProxyUrl(delight?.cover_url);
      thumb.replaceChildren();
      thumb.classList.remove("has-image", "is-text-media", "is-tag-media");
      delete thumb.dataset.platform;
      thumb.classList.toggle("has-image", Boolean(url));
      // 设置 banner 背景图（模糊用）
      const banner = $("#delightBanner");
      if (banner) banner.style.setProperty("--cover-url", url ? `url("${url}")` : "none");
      if (!delight) return;
      if (!url) {
        renderDelightFallbackMedia(thumb, delight);
        return;
      }
      // 平台徽章不依赖封面 —— 图片正常加载时也始终标明内容来源。
      const badge = document.createElement("span");
      badge.className = "platform";
      badge.textContent = platformName(delight.source_platform);
      badge.dataset.platform = String(delight.source_platform || "bilibili").toLowerCase();
      const image = document.createElement("img");
      if (isCrossOriginBase()) image.crossOrigin = "anonymous";
      image.alt = "";
      image.loading = "eager";
      image.fetchPriority = "high";
      image.decoding = "async";
      image.referrerPolicy = "no-referrer";
      image.src = url;
      image.addEventListener("error", () => {
        if (!image.isConnected || image.parentElement !== thumb) return;
        renderDelightFallbackMedia(thumb, delight);
        if (banner) banner.style.setProperty("--cover-url", "none");
      });
      thumb.append(image);
      thumb.append(badge);
    }

    function resetDelightExcerpt() {
      const wrapper = $("#delightExcerpt");
      const excerpt = $("#delightExcerptText");
      const toggle = $("#delightExcerptToggle");
      if (!wrapper || !excerpt || !toggle) return;
      wrapper.classList.remove("is-expanded");
      wrapper.hidden = true;
      excerpt.textContent = "";
      excerpt.hidden = true;
      toggle.hidden = true;
      toggle.textContent = "查看已采集的原始内容";
      toggle.setAttribute("aria-expanded", "false");
    }

    function syncDelightExcerpt(delight) {
      resetDelightExcerpt();
      const wrapper = $("#delightExcerpt");
      const excerpt = $("#delightExcerptText");
      const toggle = $("#delightExcerptToggle");
      const bodyText = String(delight?.body_text || "").trim();
      if (!wrapper || !excerpt || !toggle || !bodyText) return;
      excerpt.textContent = bodyText;
      excerpt.hidden = true;
      wrapper.hidden = false;
      toggle.hidden = false;
      toggle.setAttribute("aria-expanded", "false");
    }

    function _startDelightAutoAdvance() {
        _stopDelightAutoAdvance();
        if (state.workbenchView !== "delight") return;
        if (state.delights.length < 2) return;
        _delightAutoTimer = setInterval(() => {
            if (delightUserEngaged()) return;
            const next = state.delightIndex + 1;
            setActiveDelight(next >= state.delights.length ? 0 : next);
        }, DELIGHT_AUTO_ADVANCE_MS);
    }

    function _stopDelightAutoAdvance() {
        if (_delightAutoTimer !== null) {
            clearInterval(_delightAutoTimer);
            _delightAutoTimer = null;
      }
    }

    function delightEmptyPresentation(loadState, errorMessage = "") {
      const loading = loadState === "loading";
      const failed = loadState === "failed";
      return {
        title: loading ? "正在读取今日首选" : failed ? "今日首选暂时不可用" : "今天还没有首选",
        reason: loading
          ? "正在从本地后端读取首选，完成前不会把未知状态显示成空队列。"
          : failed
            ? (errorMessage || "读取失败，但这不代表今天没有值得关注的内容。")
            : "今天没有符合首选门槛的新内容；推荐队列仍可正常使用。",
        status: failed ? "读取失败，可以重新读取。" : loading ? "正在读取…" : "队列为空。",
        role: failed ? "alert" : "status",
        retry: failed,
      };
    }

    function renderDelightDecision(delight) {
      const meta = $("#delightDecisionMeta");
      const flags = $("#delightPriorityFlags");
      const priorityReasonTarget = $("#delightPriorityReason");
      const tagsTarget = $("#delightDecisionTags");
      const analysis = $("#delightAnalysis");
      const fallback = $("#delightReason");
      if (!delight) {
        if (meta) meta.hidden = true;
        if (analysis) analysis.hidden = true;
        return;
      }
      const priority = normalizeRecommendationPriority(delight);
      const priorityLabel = priority.label || (priority.score > 0 ? `判断优先级 ${Math.round(priority.score * 100)}` : "常规参考");
      if (flags) {
        flags.innerHTML = `<span class="queue-priority-tag" data-priority="${escapeHtml(priority.level)}" title="${escapeHtml(priority.reason || priorityLabel)}">${escapeHtml(priorityLabel)}</span><span class="queue-topic-tag">${escapeHtml(recommendationTopicLabel(delight))}</span>`;
      }
      if (priorityReasonTarget) {
        priorityReasonTarget.textContent = priority.reason;
        priorityReasonTarget.hidden = !priority.reason;
      }
      const tags = recommendationDisplayTags(delight);
      if (tagsTarget) {
        tagsTarget.innerHTML = tags.length
          ? tags.map((tag) => `<span class="detail-tag" data-tag-type="${escapeHtml(tag.type)}"><span>${escapeHtml(DISPLAY_TAG_TYPE_LABELS[tag.type] || "标签")}</span>${escapeHtml(tag.label)}</span>`).join("")
          : `<span class="detail-tag" data-tag-type="topic"><span>主题</span>${escapeHtml(recommendationTopicLabel(delight))}</span>`;
      }
      if (meta) meta.hidden = false;
      if (analysis) {
        analysis.innerHTML = recommendationDetailExplanationHtml(delight);
        analysis.hidden = false;
        bindObservationTrackingControls(analysis, delight, $("#delightStatus"));
      }
      if (fallback) fallback.hidden = true;
    }

    function syncDelightControlAvailability() {
      const count = state.delights.length;
      const hasItem = Boolean(state.delight);
      const busy = delightFeedbackInFlight;
      const safeUrl = hasItem ? delightContentUrl(state.delight) : "";
      document.querySelectorAll('[data-delight="prev"], [data-delight="next"]').forEach((button) => {
        button.disabled = busy || count <= 1;
      });
      const view = document.querySelector('[data-delight="view"]');
      if (view) {
        view.disabled = busy || !safeUrl;
        view.setAttribute("aria-disabled", view.disabled ? "true" : "false");
        view.title = safeUrl ? "阅读安全的原文链接" : "后端没有返回 HTTP(S) 原文链接";
      }
      const liked = state.delight?.state === "liked";
      document.querySelectorAll('[data-delight="like"], [data-delight="dislike"], [data-delight="dismiss"], [data-delight="chat"], [data-delight="watch-later"], [data-delight="favorite"]').forEach((button) => {
        button.disabled = busy || !hasItem || (button.dataset.delight === "like" && liked);
      });
      const retry = $("#delightRetryBtn");
      if (retry) {
        retry.hidden = state.delightLoadState !== "failed";
        retry.disabled = state.delightLoadState === "loading";
      }
    }

    function setActiveDelight(index = state.delightIndex) {
      const controls = Array.from(document.querySelectorAll("[data-delight]"));
      const newsOnlyQueue = state.delights.filter(isNewsDelightItem);
      if (newsOnlyQueue.length !== state.delights.length) {
        state.delights = newsOnlyQueue;
        state.delightIndex = Math.min(state.delightIndex, Math.max(0, newsOnlyQueue.length - 1));
      }
      if (!state.delights.length) {
        state.delight = null;
        closeDelightComposer();
        renderDelightCover(null);
        renderDelightTurns(null);
        resetDelightExcerpt();
        const presentation = delightEmptyPresentation(state.delightLoadState, state.delightLoadError);
        $("#delightTitle").textContent = presentation.title;
        $("#delightReason").hidden = false;
        $("#delightReason").textContent = presentation.reason;
        renderDelightDecision(null);
        if ($("#delightStats")) $("#delightStats").hidden = true;
        const delightPublishedEl = $("#delightPublished");
        if (delightPublishedEl) {
          delightPublishedEl.textContent = "";
          delightPublishedEl.removeAttribute("title");
          delightPublishedEl.hidden = true;
        }
        if ($("#delightStatus")) {
          $("#delightStatus").setAttribute("role", presentation.role);
          $("#delightStatus").textContent = presentation.status;
        }
        if ($("#delightCount")) $("#delightCount").textContent = "0/0";
        controls.forEach((btn) => { btn.disabled = btn.dataset.delight !== "retry"; });
        syncDelightControlAvailability();
        scheduleActivityRailHeightSync();
        return;
      }
      const shouldAnimateTransition = Boolean(state.delight);
      state.delightIndex = Math.max(0, Math.min(index, state.delights.length - 1));
      state.delight = state.delights[state.delightIndex];
      // 锁定容器高度防止下方布局跳变
      const banner = $("#delightBanner");
      if (banner && shouldAnimateTransition) {
        banner.style.height = `${banner.offsetHeight}px`;
        banner.classList.add("is-height-locked");
        banner.classList.remove("is-height-settling");
      }
      // 切换动画：先淡出，再替换内容，再淡入
      const copy = $(".delight-copy");
      const thumb = $(".delight .thumb");
      const applyContent = () => {
        closeDelightComposer();
        renderDelightCover(state.delight);
        renderDelightTurns(state.delight);
        $("#delightTitle").textContent = state.delight.title;
        renderDelightDecision(state.delight);
        syncDelightExcerpt(state.delight);
        const delightStatsEl = $("#delightStats");
        if (delightStatsEl) {
          const delightStats = recommendationStats(state.delight);
          delightStatsEl.textContent = delightStats;
          delightStatsEl.hidden = !delightStats;
        }
        const delightPublishedEl = $("#delightPublished");
        if (delightPublishedEl) {
          const published = formatPublishedTime(state.delight);
          delightPublishedEl.textContent = published;
          delightPublishedEl.title = Number.isFinite(Date.parse(state.delight.published_at))
            ? new Date(state.delight.published_at).toLocaleString()
            : "";
          delightPublishedEl.hidden = !published;
        }
        $("#delightReason").textContent = state.delight.reason;
        if ($("#delightStatus")) $("#delightStatus").textContent = state.delight.response_message || "";
        if (copy) copy.classList.remove("is-exiting");
        if (thumb) thumb.classList.remove("is-exiting");
        // 用 requestAnimationFrame 手动驱动高度动画（避免 CSS transition 启动时序问题）
        if (banner && shouldAnimateTransition) {
          if (banner._heightRaf) cancelAnimationFrame(banner._heightRaf);
          const startH = parseFloat(banner.style.height) || banner.offsetHeight;
          banner.style.height = `${startH}px`;
          banner.classList.remove("is-height-locked");
          banner.offsetHeight; // 强制 reflow：浏览器确认当前高度为 startH
          // 临时放开高度测量自然高度
          banner.style.removeProperty("height");
          const endH = banner.offsetHeight;
          if (Math.abs(endH - startH) < 0.5) {
            banner.style.removeProperty("height");
            banner.classList.remove("is-height-settling");
            return;
          }
          // 切回起始高度，开始动画
          banner.style.height = `${startH}px`;
          banner.offsetHeight;
          const duration = 200;
          const t0 = performance.now();
          const step = (now) => {
            const p = Math.min((now - t0) / duration, 1);
            const ease = 1 - (1 - p) * (1 - p); // ease-out quad
            banner.style.height = `${startH + (endH - startH) * ease}px`;
            if (p < 1) {
              banner._heightRaf = requestAnimationFrame(step);
            } else {
              banner._heightRaf = null;
              banner.style.removeProperty("height");
              banner.classList.remove("is-height-settling");
            }
          };
          banner._heightRaf = requestAnimationFrame(step);
        } else if (banner) {
          banner.style.removeProperty("height");
          banner.classList.remove("is-height-locked", "is-height-settling");
        }
      };
      if (copy && shouldAnimateTransition) copy.classList.add("is-exiting");
      if (thumb && shouldAnimateTransition) thumb.classList.add("is-exiting");
      if (shouldAnimateTransition && (copy || thumb)) {
        setTimeout(applyContent, 250);
      } else {
        applyContent();
      }
      if ($("#delightCount")) $("#delightCount").textContent = `${state.delightIndex + 1}/${state.delights.length}`;
      controls.forEach((btn) => { btn.disabled = false; });
      // Sync ☆ / ♥ pressed state for the current delight.
      const delightKey = desktopSavedItem(state.delight).item_key;
      if (delightKey && _delightStatusCache.has(delightKey)) {
        _syncDelightStatusButtons(delightKey);
      } else {
        const wlBtn = document.querySelector('[data-delight="watch-later"]');
        if (wlBtn) wlBtn.setAttribute("aria-pressed", "false");
        const favBtn = document.querySelector('[data-delight="favorite"]');
        if (favBtn) favBtn.setAttribute("aria-pressed", "false");
      }
      const likeBtn = document.querySelector('[data-delight="like"]');
      const liked = state.delight?.state === "liked";
      if (likeBtn) {
        likeBtn.setAttribute("aria-pressed", liked ? "true" : "false");
        likeBtn.disabled = liked;
      }
      syncDelightControlAvailability();
      scheduleActivityRailHeightSync();
    }

    // 鼠标/触摸拖动切换 delight
    const _DELIGHT_DRAG_DEAD_ZONE = 10;
    let _delightDragging = false;   // 按下已就绪，尚未越过死区
    let _delightDragActive = false; // 已越过死区，真正进入拖拽
    let _delightDragLastX = 0;
    function _initDelightSwipe() {
        const banner = $("#delightBanner");
        if (!banner || banner.dataset.swipeInited) return;
        banner.dataset.swipeInited = "1";
        const inner = banner.querySelector(".delight-body, .thumb");
        // 交互元素阻止事件冒泡，避免触发拖拽
        banner.querySelectorAll("button, [data-delight], input, select, textarea, a").forEach((el) => {
            el.addEventListener("pointerdown", (e) => e.stopPropagation());
        });
        banner.addEventListener("pointerdown", (e) => {
            _delightDragging = true;
            _delightDragActive = false;
            _delightSwipeStartX = e.clientX;
            _delightDragLastX = e.clientX;
            banner.setPointerCapture(e.pointerId);
            // 死区内不进入拖拽视觉态
        });
        banner.addEventListener("pointermove", (e) => {
            if (!_delightDragging) return;
            const dx = e.clientX - _delightSwipeStartX;
            // 死区判定：未越过阈值前不应用位移、不加 is-dragging
            if (!_delightDragActive) {
                if (Math.abs(dx) < _DELIGHT_DRAG_DEAD_ZONE) return;
                _delightDragActive = true;
                banner.classList.add("is-dragging");
            }
            const maxDrag = banner.offsetWidth * 0.3;
            const clamped = Math.max(-maxDrag, Math.min(maxDrag, dx));
            // 首项/末项增加阻力
            const atEdge = (dx > 0 && state.delightIndex === 0) || (dx < 0 && state.delightIndex >= state.delights.length - 1);
            const factor = atEdge ? 0.25 : 1;
            banner.style.setProperty("--drag-offset", `${clamped * factor}px`);
            _delightDragLastX = e.clientX;
        });
        banner.addEventListener("pointerup", (e) => {
            if (!_delightDragging) return;
            _delightDragging = false;
            const wasActive = _delightDragActive;
            _delightDragActive = false;
            banner.classList.remove("is-dragging");
            banner.releasePointerCapture(e.pointerId);
            const dx = e.clientX - _delightSwipeStartX;
            if (wasActive && Math.abs(dx) >= 50) {
                if (dx > 0) setActiveDelight(state.delightIndex <= 0 ? state.delights.length - 1 : state.delightIndex - 1);
                else if (dx < 0) setActiveDelight(state.delightIndex >= state.delights.length - 1 ? 0 : state.delightIndex + 1);
            }
            banner.style.removeProperty("--drag-offset");
        });
        banner.addEventListener("pointercancel", () => {
            _delightDragging = false;
            _delightDragActive = false;
            banner.classList.remove("is-dragging");
            banner.style.removeProperty("--drag-offset");
        });
    }

    let _delightVisibilityObserver = null;
    function _initDelightVisibilityObserver() {
      if (_delightVisibilityObserver) return;
      const banner = $("#delightBanner");
      if (!banner) return;
      _delightVisibilityObserver = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) _startDelightAutoAdvance();
          else _stopDelightAutoAdvance();
        }
      }, { threshold: 0.3 });
      _delightVisibilityObserver.observe(banner);
      document.addEventListener("visibilitychange", () => {
        if (document.hidden) _stopDelightAutoAdvance();
        else if (_delightVisibilityObserver) {
          // 切回时检查 banner 是否在视口内
          const rect = banner.getBoundingClientRect();
          const inView = rect.top < window.innerHeight && rect.bottom > 0;
          if (inView) _startDelightAutoAdvance();
        }
      });
    }

    function applyDelights(payload) {
      const hasQueuePayload = Array.isArray(payload?.items) || Boolean(payload?.item);
      if (!hasQueuePayload) return false;
      const items = Array.isArray(payload?.items) ? payload.items : payload.item ? [payload.item] : [];
      const normalized = sortRecommendationsByPriority(items.map(normalizeDelight).filter(Boolean));
      state.delightLoadState = normalized.length ? "ready" : "empty";
      state.delightLoadError = "";
      const previousActiveBvid = String(state.delight?.bvid || "");
      const existingByBvid = new Map(state.delights.map((item) => [String(item.bvid || ""), item]));
      state.delights = [];
      for (const item of normalized) {
        const key = String(item.bvid || "");
        if (!key) continue;
        const existingIndex = state.delights.findIndex((current) => String(current.bvid || "") === key);
        const merged = mergeDelightItem(existingByBvid.get(key) || state.delights[existingIndex], item);
        if (existingIndex >= 0) state.delights[existingIndex] = merged;
        else state.delights.push(merged);
      }
      const activePosition = previousActiveBvid
        ? state.delights.findIndex((item) => String(item.bvid || "") === previousActiveBvid)
        : -1;
      if (delightUserEngaged() && state.delight) {
        // 打字中：只同步队列数据与计数。当前卡还在队列里就更新引用；即便已被
        // 后端消费 / 过期也保留 state.delight——发送必须落在用户正对着的这张卡上。
        if (activePosition >= 0) {
          state.delightIndex = activePosition;
          state.delight = state.delights[activePosition];
        }
        syncDelightCount();
        return true;
      }
      setActiveDelight(activePosition >= 0 ? activePosition : 0);
      _startDelightAutoAdvance();
      _initDelightSwipe();
      _initDelightVisibilityObserver();
      // 批量预取 delight 队列中所有项的稍后读/收藏状态
      (async () => {
        const delightItems = state.delights.map(desktopSavedItem).filter((item) => item.item_key);
        if (!delightItems.length) return;
        await Promise.allSettled(delightItems.flatMap((item) => [
          desktopSavedMutations.hydrate("watch_later", item.item_key, () => watchLaterStatus(item)),
          desktopSavedMutations.hydrate("favorite", item.item_key, () => favoriteStatus(item))
        ]));
        for (const item of delightItems) {
          _delightStatusCache.set(item.item_key, {
            watchLater: desktopSavedMutations.isSaved("watch_later", item.item_key),
            favorite: desktopSavedMutations.isSaved("favorite", item.item_key)
          });
        }
        // 如果当前显示的 delight 缓存已就绪，立即刷新按钮状态
        const currentKey = state.delight ? desktopSavedItem(state.delight).item_key : "";
        if (currentKey && _delightStatusCache.has(currentKey)) _syncDelightStatusButtons(currentKey);
      })();
      return true;
    }

    function _syncDelightStatusButtons(itemKey) {
      const cached = _delightStatusCache.get(itemKey);
      if (!cached) return;
      const wlBtn = document.querySelector('[data-delight="watch-later"]');
      if (wlBtn) {
        wlBtn.setAttribute("aria-pressed", cached.watchLater ? "true" : "false");
        wlBtn.setAttribute("aria-label", cached.watchLater ? "取消稍后读" : "稍后读");
        wlBtn.title = cached.watchLater ? "取消稍后读" : "稍后读";
      }
      const favBtn = document.querySelector('[data-delight="favorite"]');
      if (favBtn) {
        favBtn.setAttribute("aria-pressed", cached.favorite ? "true" : "false");
        favBtn.setAttribute("aria-label", cached.favorite ? "取消收藏" : "收藏");
        favBtn.title = cached.favorite ? "取消收藏" : "收藏";
      }
    }

    function mergeMessages(items) {
      for (const raw of items) {
        const item = normalizeMessageItem(raw);
        if (!item) continue;
        const key = messageKey(item);
        if (!state.messages.some((msg) => messageKey(msg) === key)) state.messages.push(item);
      }
      renderMessages();
      applyRuntimeStatus({ unread_count: getRenderableMessages().length });
    }

    async function fetchDelightQueue() {
      state.delightLoadState = "loading";
      state.delightLoadError = "";
      if (!state.delights.length) setActiveDelight(0);
      try {
        const payload = await requestJsonStrict(ENDPOINTS.delightBatch, { timeoutMs: 15000 });
        if (!applyDelights(payload)) throw new Error("今日首选响应格式不完整，请稍后重试。");
      } catch (error) {
        state.delightLoadState = "failed";
        state.delightLoadError = configErrorMessage(error?.details) || error?.message || "读取今日首选失败，请检查本地后端连接。";
        if (!state.delights.length) setActiveDelight(0);
        else {
          const status = $("#delightStatus");
          if (status) {
            status.setAttribute("role", "alert");
            status.textContent = `刷新失败，保留当前首选：${state.delightLoadError}`;
          }
          syncDelightControlAvailability();
        }
      }
    }

    function handleRuntimeEvent(event) {
      if (!event?.type) return;
      if (RUNTIME_TRANSPORT_ONLY_EVENTS.has(event.type)) return;
      let configApplyEventAccepted = true;
      if (event.type === "config_reloaded") {
        const revision = Number(event.revision || 0);
        configApplyEventAccepted = applyConfigApplyStatus({
          state: "applied",
          requested_revision: revision,
          applied_revision: revision,
        }, { source: "runtime-event" });
      } else if (event.type === "config_reload_failed") {
        configApplyEventAccepted = applyConfigApplyStatus({
          state: "failed",
          requested_revision: Number(event.revision || 0),
          applied_revision: 0,
        }, { source: "runtime-event" });
      }
      scheduleDesktopPendingConfirmationRefresh();
      if (event.type === "refresh.pool_updated" && typeof event.pool_available_count === "number") {
        desktopRuntimeGeneration += 1;
        clearDesktopRuntimeRecovery();
      }
      applyRuntimeStatus({ ...event, live_summary: event.message || event.live_summary || event.type });
      // Credential sync changes the backend-owned source contract. Refresh it
      // immediately so the dashboard warning does not retain the pre-login
      // snapshot until settings is opened or the fallback poll runs.
      if (SOURCE_STATUS_REFRESH_EVENTS.has(event.type)) void renderSourcesStatus();
      // 库存变化事件只刷新 Tab 数字 / 空态 / 自动续页 gate，不碰已加载的推荐卡片。
      if (event.type === "refresh.pool_updated" || event.type === "pool_status") schedulePlatformAvailabilityRefresh();
      if (event.type === "degraded") {
        presentDegradedConfigRecovery({
          degraded: true,
          degraded_reason: event.reason || "",
          issues: event.issues || [],
        });
      }
      // refresh.pool_updated / recommendation.reshuffled are pool-status signals, not
      // list-replacement signals: hydrating here would wipe locally appended cards
      // (/api/recommendations only returns the latest top window). Header/pool counts
      // still update via applyRuntimeStatus above; user-initiated 换一批 / 加载更多 replace
      // the list explicitly. Matches recommend.js + popup.js (fix 79042ce).
      if (event.type === "config_reloaded" && !configApplyEventAccepted) return;
      if (event.type === "config_reload_failed") {
        if (!configApplyEventAccepted) return;
        const message = String(event.message || "后台应用配置失败，已恢复上一次生效配置。");
        if ($("#configStatus")) {
          $("#configStatus").setAttribute("role", "alert");
          $("#configStatus").value = message;
        }
        showToast("配置应用失败：请查看配置状态");
      }
      if (["init_progress", "init_failed", "init_completed"].includes(event.type)) {
        void refreshInitStatus({ schedule: event.type === "init_progress" });
      }
      if (event.type === "refresh.pool_updated" && Boolean(state.initStatus?.initialized)) {
        void refreshInitStatus({ schedule: false });
      }
      if (
        event.type === "refresh.pool_updated" &&
        state.videos.length === 0 &&
        desktopRecommendationLoadState === "failed-exhausted"
      ) {
        desktopRecommendationRecoveryAttempt = 0;
        desktopRecommendationLoadState = "failed";
      }
      if (
        event.type === "refresh.pool_updated" &&
        state.videos.length === 0 &&
        desktopRecommendationLoadState === "failed"
      ) {
        scheduleDesktopRecommendationRecovery();
      }
      if (event.type === "activity.added") scheduleActivityPageRefresh();
      if (
        event.type === "profile_updated" ||
        event.type === "interest.confirmed" ||
        event.type === "interest.rejected" ||
        event.type === "interest.chat" ||
        event.type === "avoidance.confirmed" ||
        event.type === "avoidance.rejected" ||
        event.type === "avoidance.chat"
      ) void refreshProfile();
      if (event.type === "delight.candidate" && event.bvid) {
        const delight = normalizeDelight(event);
        if (delight) {
          state.delightLoadState = "ready";
          state.delightLoadError = "";
          const key = String(delight.bvid || "");
          const existingIndex = state.delights.findIndex((item) => String(item.bvid || "") === key);
          if (existingIndex >= 0) {
            state.delights[existingIndex] = mergeDelightItem(state.delights[existingIndex], delight);
            if (state.delight && String(state.delight.bvid || "") === key) {
              if (delightUserEngaged()) {
                // 正在这张卡上打字：只更新数据引用，不重渲染（重渲染会收起输入框）。
                state.delight = state.delights[existingIndex];
              } else {
                setActiveDelight(existingIndex);
              }
            }
          } else {
            state.delights.push(delight);
            if (delightUserEngaged()) {
              // 用户正在当前卡的聊天框里打字：新候选只静默入队并更新计数，
              // 不抢走当前卡——否则输入被收起、随后的发送还会串到新卡上。
              syncDelightCount();
            } else {
              setActiveDelight(state.delights.length - 1);
            }
          }
        }
      }
      if (event.type === "delight.refreshed") scheduleDelightQueueRefresh();
      if (event.type === "delight.liked") {
        const data = event.data || event;
        const bvid = String(data.bvid || data.domain || event.bvid || event.domain || "");
        const index = state.delights.findIndex((item) => String(item.bvid || "") === bvid);
        if (index >= 0) {
          state.delights[index] = {
            ...state.delights[index],
            state: "liked",
            response_message: String(data.message || event.message || "好，这类多来点。"),
          };
          if (state.delight && String(state.delight.bvid || "") === bvid) {
            setActiveDelight(index);
          }
        }
      }
      if (event.type === "notification.pending" && event.bvid) mergeMessages([{ ...event, type: "notification" }]);
      if (event.type === "interest.probe" && event.domain) mergeMessages([{ type: "interest.probe", domain: event.domain, reason: event.reason || event.message || "后端希望确认这个兴趣方向。", specifics: event.specifics || event.examples || [], probe_mode: event.probe_mode || "", challenge: Boolean(event.challenge) }]);
      if (event.type === "avoidance.probe" && event.domain) mergeMessages([{ type: "avoidance.probe", domain: event.domain, reason: event.reason || event.message || "后端希望确认这个避雷方向。", specifics: event.specifics || event.examples || [], probe_mode: event.probe_mode || "", challenge: Boolean(event.challenge) }]);
    }

    function scheduleDesktopRuntimeReconnect() {
      if (document.hidden || desktopRuntimeReconnectTimer !== null) return;
      desktopRuntimeReconnectTimer = window.setTimeout(() => {
        desktopRuntimeReconnectTimer = null;
        connectRuntimeStream();
      }, 3000);
    }

    function connectRuntimeStream() {
      if (document.hidden) return;
      if (desktopRuntimeReconnectTimer !== null) {
        window.clearTimeout(desktopRuntimeReconnectTimer);
        desktopRuntimeReconnectTimer = null;
      }
      if (state.runtimeSocket && [WebSocket.CONNECTING, WebSocket.OPEN].includes(state.runtimeSocket.readyState)) {
        return;
      }
      if (state.runtimeSocket) state.runtimeSocket.close();
      try {
        const socket = new WebSocket(getRuntimeStreamUrl());
        state.runtimeSocket = socket;
        socket.addEventListener("open", () => {
          $("#statusLabel").textContent = "实时连接正常";
          setSideHealthState(state.degraded ? "degraded" : state.runtimeStatus?.initialized === false ? "uninitialized" : "online");
          restartDesktopFailedRecoveries();
          scheduleDesktopPendingConfirmationRefresh();
          void refreshConfigApplyStatus();
          // The page may load before the backend binds (frozen-entry launch
          // race): the boot hydrate then swallows every failure into nulls and
          // nothing else ever re-fetches — an uninitialized backend emits no
          // runtime events, so the guided-init card would stay hidden forever.
          // First successful (re)connect with no backend data yet → hydrate.
          // Scoped to the never-hydrated case so transient reconnects don't
          // wipe locally appended recommendation cards (see fix 79042ce).
          if (!state.initStatus && !state.runtimeStatus) {
            void ensureAuthenticated()
              .then(scheduleBackendHydration)
              .catch(() => {});
          }
        });
        socket.addEventListener("message", (event) => {
          try {
            const payload = JSON.parse(event.data);
            if (payload?.type === "runtime.heartbeat") {
              $("#statusLabel").textContent = "实时连接正常";
              setSideHealthState(state.degraded ? "degraded" : state.runtimeStatus?.initialized === false ? "uninitialized" : "online");
              return;
            }
            handleRuntimeEvent(payload);
          } catch {}
        });
        socket.addEventListener("close", (event) => {
          if (state.runtimeSocket === socket) {
            state.runtimeSocket = null;
            $("#statusLabel").textContent = "实时流重连中";
            setSideHealthState("offline", "重连中");
            console.info("runtime-stream closed; reconnect scheduled", {
              code: event.code,
              reason: event.reason || "",
              clean: event.wasClean
            });
            scheduleDesktopRuntimeReconnect();
          }
        });
        socket.addEventListener("error", () => {
          if (state.runtimeSocket === socket) {
            $("#statusLabel").textContent = "实时流重连中";
            setSideHealthState("offline", "重连中");
          }
        });
      } catch {
        $("#statusLabel").textContent = "实时流重连中";
        setSideHealthState("offline", "重连中");
        scheduleDesktopRuntimeReconnect();
      }
    }

    function pauseDesktopBackendSession() {
      backendHydrationPending = true;
      if (desktopRuntimeReconnectTimer !== null) {
        window.clearTimeout(desktopRuntimeReconnectTimer);
        desktopRuntimeReconnectTimer = null;
      }
      for (const timer of [
        backendHydrationTimer,
        desktopRecommendationRecoveryTimer,
        desktopRuntimeRecoveryTimer,
        platformAvailabilityRetryTimer,
        configSnapshotRetryTimer,
        activityPageRefreshTimer,
      ]) {
        if (timer !== null) window.clearTimeout(timer);
      }
      backendHydrationTimer = null;
      desktopRecommendationRecoveryTimer = null;
      desktopRuntimeRecoveryTimer = null;
      platformAvailabilityRetryTimer = null;
      configSnapshotRetryTimer = null;
      activityPageRefreshTimer = null;
      clearInitPolling();
      const socket = state.runtimeSocket;
      state.runtimeSocket = null;
      if (socket) socket.close();
    }

    async function startDesktopBackendSession({ forceHydrate = false } = {}) {
      if (document.hidden || desktopBackendSessionInFlight) return;
      desktopBackendSessionInFlight = true;
      try {
        await ensureAuthenticated();
        const stale = Date.now() - desktopLastHydratedAt >= DESKTOP_RESUME_HYDRATE_TTL_MS;
        if (forceHydrate || backendHydrationPending || !desktopLastHydratedAt || stale) {
          // forceHydrate 只有首屏引导会传：那时列表本来就是空的。切回标签页触发的
          // 再水合不带这个标志，列表原样保留。
          await hydrateFromBackend({ replaceRecommendations: forceHydrate });
          desktopLastHydratedAt = Date.now();
          backendHydrationPending = false;
        }
        if (!document.hidden) connectRuntimeStream();
        if (forceHydrate) await refreshMigrationStatus({ force: true });
      } catch (error) {
        console.error("后端数据加载失败", error);
        if (!state.videos.length && desktopRecommendationLoadState === "loading") {
          desktopRecommendationLoadState = "failed";
          scheduleDesktopRecommendationRecovery();
          renderVideos();
        }
        $("#statusLabel").textContent = "后端数据加载失败";
        $("#runtimeSummary").textContent = error?.message || "页面已保留离线数据，可打开设置检查 FastAPI 地址。";
        showToast("后端数据加载失败，页面已保留离线数据");
        if (!document.hidden) connectRuntimeStream();
      } finally {
        desktopBackendSessionInFlight = false;
      }
    }

    async function refreshProfile() {
      const payload = await requestJson(ENDPOINTS.profile);
      const profile = payload?.profile || payload;
      if (profile && profile.initialized !== false) {
        state.profile = profile;
        hydrateInboxFromSpeculations(profile.speculative_interests);
        hydrateInboxFromSpeculations(profile.speculative_avoidances, "avoidance.probe");
        renderRail();
        renderProfileDetails();
        renderMessages();
      }
    }

    // replaceRecommendations 只留给「用户明确要求换一批列表」的调用方：首屏引导和
    // 手动刷新。后台再水合（切回标签页、config_reloaded、保存配置、初始化完成）一律
    // 保持 false —— /api/recommendations 只返回最新的 top 窗口，整表覆盖会把用户
    // 滚动加载出来的卡片全部丢掉并按后端最新排序重排（群反馈的「重新排序」）。
    // replace=false 时 applyDesktopRecommendationSnapshot 只在列表为空时装填。
    async function hydrateFromBackend({ replaceRecommendations = false } = {}) {
      const firstRuntimeGeneration = desktopRuntimeGeneration;
      let runtimeReconciliationGeneration = null;

      function applyInitialRecommendations(items) {
        applyDesktopRecommendationSnapshot(items, { replace: replaceRecommendations });
        renderFilters();
        renderVideos();
        scheduleAutoLoadCheck();
      }

      function markDesktopRecommendationFailedAndRecover(error) {
        if (state.videos.length > 0) {
          clearDesktopRecommendationRecovery("ready");
          return;
        }
        // A mid-session LLM-registry degrade blocks /api/recommendations with a
        // 503 {status:"degraded", …} envelope, which requestJsonStrict rethrows
        // as error.details (§4.8). Route that to the model-settings recovery
        // instead of the generic retry UI — the pool cannot refill until the
        // provider is fixed, so a retry loop here is a dead end.
        const details = error && error.details;
        if (details && details.status === "degraded") {
          presentDegradedConfigRecovery({
            degraded: true,
            degraded_reason: details.reason || "",
            issues: details.issues || [],
          });
          return;
        }
        desktopRecommendationLoadState = "failed";
        scheduleDesktopRecommendationRecovery();
        renderVideos();
      }

      function readRuntimeSnapshot() {
        return readRuntimeStatusSnapshot();
      }

      function applyInitialRuntimeSnapshot(snapshot) {
        if (firstRuntimeGeneration !== desktopRuntimeGeneration) return;
        try {
          const applied = applyDesktopRuntimeSnapshot(snapshot, firstRuntimeGeneration);
          if (applied && runtimeReconciliationGeneration === firstRuntimeGeneration) {
            runtimeReconciliationGeneration = desktopRuntimeGeneration;
          }
          if (applied && grid.querySelector(".init-onboarding")) renderVideos();
        } catch {
          markDesktopRuntimeFailedAndRecover();
        }
      }

      function markDesktopRuntimeFailedAndRecover() {
        if (firstRuntimeGeneration !== desktopRuntimeGeneration) return;
        desktopRuntimeLoadState = "failed";
        scheduleDesktopRuntimeRecovery();
        renderDesktopRuntimeFailure();
      }

      function applyHealthSnapshot(snapshot) {
        if (!snapshot) return;
        const service = String(snapshot.service || "");
        if (service) setNewsClawProductMode(service === "newsclaw-api");
        if (snapshot.degraded === true) {
          presentDegradedConfigRecovery({
            degraded: true,
            degraded_reason: snapshot.degraded_reason || "",
            issues: snapshot.issues || [],
          });
          return;
        }
        $("#statusLabel").textContent = "已连接本地后端";
        setSideHealthState(state.runtimeStatus?.initialized === false ? "uninitialized" : "online");
      }
      function applyInitStatusSnapshot(snapshot) {
        if (!snapshot) return;
        state.initStatus = snapshot;
        renderSettingsReinitStatus();
        renderVideos();
        // Re-attach the init poll if a run is live at load time. Hydrate only
        // fetches init-status once, while the poll observes quiet heartbeats
        // when runtime events are unavailable.
        if (snapshot.running || embeddingPullNeedsPolling(snapshot)) {
          scheduleInitStatusRefresh(INIT_STATUS_POLL_MS);
        }
      }

      function applyActivitySnapshot(snapshot) {
        if (!snapshot) return;
        state.activity = snapshot;
        state.activityItems = asArray(snapshot.items);
        state.activityCursor = snapshot.next_cursor || snapshot.next || "";
        state.activityHasMore = Boolean(snapshot.has_more && state.activityCursor);
        renderRail();
        renderActivityHistory();
      }

      function applyProfileSnapshot(snapshot) {
        const profile = snapshot?.profile || snapshot;
        if (!profile || profile.initialized === false) return;
        state.profile = profile;
        hydrateInboxFromSpeculations(profile.speculative_interests);
        hydrateInboxFromSpeculations(profile.speculative_avoidances, "avoidance.probe");
        renderRail();
        renderProfileDetails();
        renderMessages();
      }

      function applyDelightSnapshot(snapshot) {
        applyDelights(snapshot);
      }

      function applyNotificationSnapshot(snapshot) {
        if (snapshot?.item) mergeMessages([{ ...snapshot.item, type: "notification" }]);
      }

      function applyChatSnapshot(snapshot) {
        // Use the same scoped durable-turn renderer as later refreshes. The
        // initial snapshot must not briefly show delight-only history or
        // flatten probe turns into an untracked user/assistant pair.
        applyDialogueChatSnapshot(snapshot);
      }

      function applyDelightChatSnapshot(snapshot) {
        const items = Array.isArray(snapshot) ? snapshot : asArray(snapshot?.items);
        for (const turn of items.filter(Boolean)) {
          applyTurnToDelight({ ...turn, scope: turn.scope || "delight" });
        }
      }

      async function reconcileRuntimeAfterRecommendations() {
        const secondRuntimeGeneration = desktopRuntimeGeneration;
        runtimeReconciliationGeneration = secondRuntimeGeneration;
        try {
          const applied = applyDesktopRuntimeSnapshot(
            await readRuntimeSnapshot(),
            runtimeReconciliationGeneration
          );
          if (applied && grid.querySelector(".init-onboarding")) renderVideos();
        } catch {
          // Keep the first successful runtime snapshot (or a newer stream
          // update). If both boot reads failed, its resource-level recovery is
          // already scheduled by markDesktopRuntimeFailedAndRecover().
          if (runtimeReconciliationGeneration !== desktopRuntimeGeneration) return;
          if (desktopRuntimeLoadState === "failed") {
            scheduleDesktopRuntimeRecovery();
            renderDesktopRuntimeFailure();
          }
        }
      }

      // /api/ping is deliberately provider-free and carries recovery
      // metadata only when the backend is degraded. Pay one loopback RTT
      // before normal parallel hydration so a broken LLM registry does not
      // generate a console storm from intentionally-blocked business APIs.
      const pingSnapshot = await requestJson(ENDPOINTS.ping);
      applyHealthSnapshot(pingSnapshot);
      if (pingSnapshot?.degraded === true) {
        void requestJson(ENDPOINTS.health).then(applyHealthSnapshot).catch(() => {});
        const configSnapshot = await requestJson(ENDPOINTS.config);
        applyConfigSnapshot(configSnapshot);
        if (!configSnapshot) scheduleConfigSnapshotRetry();
        if (!state.videos.length) {
          desktopRecommendationLoadState = "failed-exhausted";
          renderVideos();
        }
        return;
      }

      // The chat badge is tiny but high-signal: start it before recommendation
      // cards fan out into saved-status reads, otherwise a healthy 10ms request
      // can sit behind the first-screen connection queue for several seconds.
      const pendingConfirmationsPromise = refreshDesktopPendingConfirmations();
      const recommendationsPromise = readRecommendationSnapshot();
      const runtimePromise = readRuntimeSnapshot();

      const recommendationApplicationPromise = recommendationsPromise.then(
        (items) => applyInitialRecommendations(items),
        (error) => markDesktopRecommendationFailedAndRecover(error),
      );
      const runtimeApplicationPromise = runtimePromise.then(
        (snapshot) => applyInitialRuntimeSnapshot(snapshot),
        () => markDesktopRuntimeFailedAndRecover(),
      );
      const runtimeReconciliationPromise = recommendationApplicationPromise.then(
        () => reconcileRuntimeAfterRecommendations(),
        () => reconcileRuntimeAfterRecommendations(),
      );

      const secondaryPromises = [
        pendingConfirmationsPromise,
        syncWatchLaterButtons(),
        syncFavoriteButtons(),
        refreshTrackedObservations({ silent: true }),
        requestJson(ENDPOINTS.health).then(applyHealthSnapshot),
        requestJson(ENDPOINTS.initStatus).then(applyInitStatusSnapshot),
        requestJson(`${ENDPOINTS.activityFeed}?limit=5`).then(applyActivitySnapshot),
        requestJson(ENDPOINTS.profile).then(applyProfileSnapshot),
        fetchDelightQueue(),
        requestJson(ENDPOINTS.notificationPending).then(applyNotificationSnapshot),
        requestJson(`${ENDPOINTS.chatTurns}?session=${encodeURIComponent(SHARED_CHAT_SESSION)}&limit=100`).then(applyChatSnapshot),
        requestJson(`${ENDPOINTS.chatTurns}?session=${encodeURIComponent(SHARED_CHAT_SESSION)}&scope=delight&limit=80`).then(applyDelightChatSnapshot),
        loadConfigSnapshot(),
        refreshPlatformAvailability(),
      ];

      // 预取 LAN IP，供二维码面板使用；它不参与任一首屏资源的应用顺序。
      requestJson(ENDPOINTS.qrInfo).then((info) => { if (info?.lan_ip) _cachedLanIp = info.lan_ip; }).catch(() => {});
      await Promise.allSettled(secondaryPromises);
      await validateDialogueContext({ announce: true });
      renderDialogueContextBar();
      await Promise.allSettled([
        recommendationApplicationPromise,
        runtimeApplicationPromise,
        runtimeReconciliationPromise,
      ]);
    }

    // preserveVideos：跳过推荐网格这一步。给「高频后台事件顺手带起来的重绘」用，
    // 它们只想刷新头部 / 库存 / 侧栏，不该碰用户正在浏览的列表。
    function renderAll({ preserveVideos = false } = {}) {
      const steps = [renderFilters, renderVideos, syncSourceMetric, renderRail, renderProfileDetails, renderMessages, renderChat, renderPoolStatus];
      for (const step of steps) {
        if (preserveVideos && step === renderVideos) continue;
        try { step(); } catch (error) { showFatal(error, step.name || "渲染"); }
      }
      scheduleActivityRailHeightSync();
      scheduleAutoLoadCheck();
    }

    function buildConfigUpdate() {
      const logPath = splitLogPath(getInput("logPath"), state.config?.logging);
      const embeddingFallbackProvider = getInput("embeddingFallbackProvider");
      const embedding = {
        provider: $("#embeddingProvider").value,
        fallback_enabled: Boolean(embeddingFallbackProvider),
        fallback_provider: embeddingFallbackProvider,
        model: getInput("embeddingModel"),
        output_dimensionality: Math.max(0, getIntInput("embeddingOutputDimensionality", 1024)),
        similarity_threshold: getFloatInput("embeddingSimilarity", 0.82)
      };
      if (getInput("embeddingApiKey")) embedding.api_key = getInput("embeddingApiKey");
      if (getInput("embeddingBaseUrl")) embedding.base_url = getInput("embeddingBaseUrl");
      const llmDraft = state.llmDraft || normalizeLlmDraft(state.config?.llm || {});
      const llm = {
        routing_version: 2,
        instances: clonePlain(llmDraft.instances),
        default_chain: [...llmDraft.default_chain],
        routes: Object.fromEntries(
          Object.entries(llmDraft.routes).map(([moduleName, route]) => [
            moduleName,
            {
              inherit: route.inherit !== false,
              chain: route.inherit !== false ? [] : [...route.chain]
            }
          ])
        ),
        concurrency: getIntInput("llmConcurrency", 4),
        timeout: getIntInput("llmTimeout", 1200),
        embedding: { ...(state.config?.llm?.embedding || {}), ...embedding }
      };
      return {
        language: getInput("language") || "zh",
        data_dir: getInput("dataDir"),
        llm,
        scheduler: {
          enabled: $("#schedulerEnabled").value === "on",
          pause_on_extension_disconnect: $("#pauseDisconnect").value === "pause",
          extension_disconnect_grace_seconds: getIntInput("extensionDisconnectGrace", 90),
          pool_target_count: getIntInput("poolTarget", 300),
          account_sync_interval_hours: getIntInput("accountSyncInterval", 6),
          refresh_check_interval_seconds: getIntInput("refreshCheckInterval", 60),
          signal_event_threshold: getIntInput("signalEventThreshold", 6),
          feedback_batch_threshold: getIntInput("feedbackBatchThreshold", 3),
          trending_refresh_minutes: getIntInput("trendingRefreshMinutes", 3),
          explore_refresh_minutes: getIntInput("exploreRefreshMinutes", 3),
          discovery_limit: getIntInput("discoveryLimit", 30),
          delight_queue_limit: getDelightQueueLimit(),
          proactive_push_interval_seconds: getIntInput("proactivePushInterval", 120),
          speculator_idle_interval_minutes: getIntInput("speculatorIdleInterval", 30),
          speculation_interval_minutes: getIntInput("speculationInterval", 10),
          speculation_ttl_days: getIntInput("speculationTtl", 3),
          speculation_cooldown_days: getIntInput("speculationCooldown", 7),
          speculation_confirmation_threshold: getIntInput("speculationThreshold", 3),
          speculation_max_active: getIntInput("speculationMaxActive", 5),
          speculation_max_primary_interests: getIntInput("speculationMaxPrimary", 15),
          speculation_max_secondary_interests: getIntInput("speculationMaxSecondary", 60),
          auto_update_enabled: $("#autoUpdate").value === "on",
          auto_update_check_interval_hours: getIntInput("autoUpdateInterval", 6)
        },
        // NewsClaw does not run the inherited video discovery, keyframe,
        // danmaku, cover-evaluation or search-keyword pipelines. Preserve
        // those compatibility values byte-for-byte instead of fabricating
        // controls that silently overwrite them during an unrelated save.
        discovery: { ...(state.config?.discovery || {}) },
        storage: { ...(state.config?.storage || {}) },
        network: { mode: getInput("networkProxyMode"), proxy: getInput("networkProxy") },
        logging: {
          level: getInput("logLevel") || "INFO",
          file_level: getInput("logFileLevel") || "DEBUG",
          directory: logPath.directory,
          filename: logPath.filename,
          file_path: getInput("logPath"),
          max_file_size_mb: getIntInput("logMaxFileSize", 100),
          backup_count: getIntInput("logBackupCount", 1),
          aggregate_budget_mb: getIntInput("logAggregateBudget", 500),
          unmanaged_truncate_mb: getIntInput("logUnmanagedTruncate", 200),
          unmanaged_max_age_days: getIntInput("logUnmanagedMaxAge", 30)
        }
      };
    }

    // Shared by update checks and account-sync status: the backend hands out
    // raw ISO strings (UTC, microseconds), which are unreadable as-is.
    function formatLocalTime(iso) {
      if (!iso) return "";
      const date = new Date(iso);
      if (Number.isNaN(date.getTime())) return "";
      return date.toLocaleString("zh-CN", { hour12: false });
    }

    function formatProbeResult(result) {
      const ok = Boolean(result?.ok);
      const instance = result?.instance_id ? ` ${result.instance_id}` : "";
      const provider = result?.provider ? ` · ${result.provider}` : "";
      const model = result?.model ? ` / ${result.model}` : "";
      const latency = Number.isFinite(Number(result?.latency_ms)) && Number(result.latency_ms) > 0
        ? ` (${Math.round(Number(result.latency_ms))}ms)`
        : "";
      const detail = result?.message || result?.error || (ok ? "服务可用" : "服务不可用");
      return `${ok ? "可用" : "不可用"}${instance}${provider}${model}${latency}: ${detail}`;
    }

    async function probeConfigService(kind, config, instanceId = "") {
      return await requestJsonStrict(ENDPOINTS.configProbe, {
        method: "POST",
        // Backend allows a bounded 120s probe so cold local Ollama models can
        // finish their startup retry window. The browser must outlive it.
        timeoutMs: 125000,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, instance_id: String(instanceId || ""), config })
      });
    }

    // One DOM convention for every "click, wait, read a verdict" strip on this
    // page: tone in the dataset, verdict in the text. The LLM/embedding probes
    // and the per-source 测试连接 buttons share it instead of each keeping a
    // private copy — two independent copies of one rendering rule is exactly
    // the drift that left the codebase with two divergent source status maps.
    function setProbeStatus(statusEl, tone, text) {
      if (!statusEl) return;
      statusEl.dataset.tone = tone;
      statusEl.textContent = text;
    }

    function renderProbeResult(statusEl, result) {
      if (!statusEl) return;
      setProbeStatus(statusEl, result?.ok ? "success" : "error", formatProbeResult(result));
      const configStatus = $("#configStatus");
      if (configStatus) configStatus.value = formatProbeResult(result);
    }

    function renderProbePending(statusEl, label) {
      setProbeStatus(statusEl, "pending", `${label} 探测中…`);
    }

    async function runLlmInstanceProbe(instanceId) {
      if (!state.llmDraft?.instances[instanceId]) return;
      state.llmProbeResults.set(instanceId, { pending: true });
      renderLlmInstances();
      try {
        const result = await probeConfigService("llm_instance", buildConfigUpdate(), instanceId);
        state.llmProbeResults.set(instanceId, result);
      } catch (error) {
        state.llmProbeResults.set(instanceId, {
          ok: false,
          error: configErrorMessage(error?.details) || error?.message || "实例探测失败"
        });
      }
      renderLlmInstances();
    }

    async function runLlmChainProbe() {
      const button = $("#probeLlmChain");
      const statusEl = $("#probeLlmChainStatus");
      if (button) button.disabled = true;
      renderProbePending(statusEl, "默认调用链");
      try {
        const result = await probeConfigService("llm_chain", buildConfigUpdate());
        renderProbeResult(statusEl, result);
      } catch (error) {
        renderProbeResult(statusEl, {
          ok: false,
          error: configErrorMessage(error?.details) || error?.message || "调用链探测失败"
        });
      } finally {
        if (button) button.disabled = false;
      }
    }

    async function runEmbeddingConfigProbe() {
      const button = $("#probeEmbedding");
      const statusEl = $("#probeEmbeddingStatus");
      if (button) button.disabled = true;
      renderProbePending(statusEl, "Embedding");
      try {
        const result = await probeConfigService("embedding", buildConfigUpdate());
        renderProbeResult(statusEl, result);
      } catch (error) {
        renderProbeResult(statusEl, {
          ok: false,
          error: configErrorMessage(error?.details) || error?.message || "Embedding 探测失败"
        });
      } finally {
        if (button) button.disabled = false;
      }
    }

    async function runNetworkProxyConfigProbe() {
      const button = $("#probeNetworkProxy");
      const statusEl = $("#probeNetworkProxyStatus");
      if (button) button.disabled = true;
      renderProbePending(statusEl, "代理");
      try {
        const result = await probeConfigService("network_proxy", { network: { mode: getInput("networkProxyMode"), proxy: getInput("networkProxy") } });
        renderProbeResult(statusEl, result);
      } catch (error) {
        renderProbeResult(statusEl, {
          ok: false,
          error: configErrorMessage(error?.details) || error?.message || "代理探测失败"
        });
      } finally {
        if (button) button.disabled = false;
      }
    }

    document.addEventListener("click", (event) => {
      const closeId = event.target?.dataset?.close;
      if (closeId) closePanel(closeId);
    });

    function setActiveSettingsPanel(panelName = "models") {
      const tabs = [...document.querySelectorAll("[data-settings-tab]")];
      tabs.forEach((tab) => {
        const isActive = tab.dataset.settingsTab === panelName;
        tab.classList.toggle("is-active", isActive);
        tab.setAttribute("aria-selected", isActive ? "true" : "false");
        tab.tabIndex = isActive ? 0 : -1;
      });
      document.querySelectorAll("[data-settings-panel]").forEach((panel) => {
        const isActive = panel.dataset.settingsPanel === panelName;
        panel.hidden = !isActive;
        panel.setAttribute("aria-hidden", isActive ? "false" : "true");
      });
      if (panelName === "general") void refreshMigrationStatus({ force: true });
    }

    document.querySelectorAll("[data-settings-tab]").forEach((tab) => {
      tab.addEventListener("click", () => setActiveSettingsPanel(tab.dataset.settingsTab));
      tab.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        const tabs = [...document.querySelectorAll("[data-settings-tab]")];
        const currentIndex = tabs.indexOf(tab);
        if (currentIndex < 0 || !tabs.length) return;
        event.preventDefault();
        const nextIndex = event.key === "Home"
          ? 0
          : event.key === "End"
            ? tabs.length - 1
            : (currentIndex + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
        const nextTab = tabs[nextIndex];
        setActiveSettingsPanel(nextTab.dataset.settingsTab);
        nextTab.focus();
      });
    });

    function setActiveModelSettingsPanel(groupName = "llm", panelName = "default") {
      document.querySelectorAll(`[data-model-settings-tab][data-model-settings-group="${groupName}"]`).forEach((tab) => {
        const isActive = tab.dataset.modelSettingsTab === panelName;
        tab.classList.toggle("is-active", isActive);
        tab.setAttribute("aria-selected", isActive ? "true" : "false");
      });
      document.querySelectorAll(`[data-model-settings-panel][data-model-settings-group="${groupName}"]`).forEach((panel) => {
        panel.hidden = panel.dataset.modelSettingsPanel !== panelName;
      });
    }

    document.querySelectorAll("[data-model-settings-tab]").forEach((tab) => {
      tab.addEventListener("click", () => setActiveModelSettingsPanel(tab.dataset.modelSettingsGroup, tab.dataset.modelSettingsTab));
    });

    function startChatPlaceholderRotation() {
      const input = $("#chatInput");
      if (!input || chatPlaceholderTimer) return;
      chatPlaceholderTimer = window.setInterval(() => {
        if (document.activeElement === input || input.value.trim()) return;
        chatPlaceholderIndex = (chatPlaceholderIndex + 1) % CHAT_PLACEHOLDERS.length;
        input.setAttribute("placeholder", CHAT_PLACEHOLDERS[chatPlaceholderIndex]);
      }, 5000);
    }

    try {
      const eventWorkspace = globalThis.NewsClawEventWorkspace;
      if (!eventWorkspace?.createController) throw new Error("event-workspace helper did not load");
      eventWorkspaceController = eventWorkspace.createController({
        requestJsonStrict,
        endpoints: {
          events: ENDPOINTS.newsEvents,
          changes: ENDPOINTS.newsChanges,
          conflicts: ENDPOINTS.newsConflicts,
        },
        openMaterials: openNewMaterialsPage,
        openTracking: () => openContentLibraryPage("tracking", { forceLoad: true }),
        showToast,
        onUnreadChange: ({ unreadChangeCount = 0, unreadConflictCount = 0 }) => {
          const badge = $("#eventUnreadBadge");
          if (!badge) return;
          const total = Math.max(0, Number(unreadChangeCount || 0) + Number(unreadConflictCount || 0));
          badge.textContent = total > 99 ? "99+" : String(total);
          badge.hidden = total <= 0;
        },
      });
      void eventWorkspaceController.refreshSummary();
    } catch (error) {
      console.error("事件脉冲台初始化失败", error);
      showFatal(error, "初始化事件脉冲台");
    }

    function applyHomeSearch(value) {
      const query = String(value || "");
      if (state.homeMode === "events") eventWorkspaceController?.applySearch(query);
      else {
        state.query = query;
        renderAll();
      }
      const desktopInput = $("#searchInput");
      const mobileInput = $("#mobileSearchInput");
      if (desktopInput && desktopInput.value !== query) desktopInput.value = query;
      if (mobileInput && mobileInput.value !== query) mobileInput.value = query;
    }

    safeBind("#sideDrawerBtn", "click", toggleSideDrawer);
    safeBind(".brand", "click", (event) => { event.preventDefault(); openHomePage("events"); });
    safeBind("#sideDrawerScrim", "click", closeSideDrawer);
    safeBind("#mobileMenuBtn", "click", openMobileMenu);
    safeBind("#mobileMenuClose", "click", closeMobileMenu);
    safeBind("#mobileSearchInput", "input", (event) => applyHomeSearch(event.target.value));
    safeBind("#mobileSearchForm", "submit", (event) => { event.preventDefault(); applyHomeSearch($("#mobileSearchInput")?.value || ""); closeMobileMenu(); });
    document.querySelectorAll("[data-mobile-panel]").forEach((button) => {
      button.addEventListener("click", () => openMobilePanel(button.dataset.mobilePanel, { settingsPanel: button.dataset.settings }));
    });
    document.querySelectorAll("[data-mobile-page]").forEach((button) => {
      button.addEventListener("click", () => {
        openMobilePage(button.dataset.mobilePage, { settingsPanel: button.dataset.settings });
      });
    });
    document.querySelectorAll("[data-mobile-home-mode]").forEach((button) => {
      button.addEventListener("click", () => openHomePage(button.dataset.mobileHomeMode));
    });
    document.querySelectorAll("[data-mobile-back]").forEach((button) => {
      button.addEventListener("click", returnToMobileMenu);
    });

    const MOBILE_QR_SEEN_KEY = "openbiliclaw.webui.mobileQrSeen";
    function markMobileQrSeen() {
      storageSet(MOBILE_QR_SEEN_KEY, "1");
      const dot = $("#mobileQrDot");
      const callout = $("#mobileQrCallout");
      if (dot) dot.hidden = true;
      if (callout) callout.hidden = true;
    }
    function initMobileQrDiscovery() {
      if (storageGet(MOBILE_QR_SEEN_KEY)) return;
      const dot = $("#mobileQrDot");
      const callout = $("#mobileQrCallout");
      if (dot) dot.hidden = false;
      if (callout) {
        callout.hidden = false;
        // Quiet down on its own — the dot keeps marking the entry until the
        // drawer is actually opened once.
        window.setTimeout(() => { callout.hidden = true; }, 15000);
      }
    }
    async function openMobileQrDrawer() {
      markMobileQrSeen();
      openPanel("mobileQrDrawer");
      const canvas = $("#mobileQrCanvas");
      const urlEl = $("#mobileQrUrl");
      const hintEl = $("#mobileQrHint");
      const copyButton = $("#mobileQrCopyBtn");
      const qr = window.OBCMobileQr;
      if (!canvas || !urlEl || !hintEl || !qr) return;
      canvas.textContent = "";
      urlEl.textContent = "正在获取局域网地址…";
      hintEl.hidden = true;
      hintEl.textContent = "";
      if (copyButton) copyButton.disabled = true;
      const def = locationApiDefault();
      const pageHostIsReachable = !qr.isLoopbackMobileHost(def.host);
      // The backend knows its own LAN IP; the page host may be 127.0.0.1,
      // which a phone cannot reach. Always re-query on open: the address moves
      // when the user switches Wi-Fi or plugs in a dongle, and a sticky cache
      // would keep encoding an unreachable host until a full page reload. The
      // page-load prefetch is only a fallback for when this request fails.
      let qrInfo = null;
      try {
        qrInfo = await requestJsonStrict(ENDPOINTS.qrInfo, { timeoutMs: 10000 });
      } catch (error) {
        if (!pageHostIsReachable) {
          canvas.textContent = "无法确认手机访问条件。";
          urlEl.textContent = "当前只确认本机页面可用";
          hintEl.textContent = `局域网状态读取失败：${error?.message || "请检查本机后端"}`;
          hintEl.hidden = false;
          return;
        }
      }
      const freshLanIp = String(qrInfo?.lan_ip || "").trim();
      if (freshLanIp) _cachedLanIp = freshLanIp;
      const lanIp = freshLanIp || _cachedLanIp;
      const typedHost = (storageGet("openbiliclaw.webui.backendHost") || "").trim();
      const typedPort = (storageGet("openbiliclaw.webui.backendPort") || "").trim();
      // A non-loopback page origin is already the address that reached this
      // backend (including a public HTTPS gateway). Keep that origin instead
      // of replacing it with the backend's private LAN IP. Loopback pages still
      // need the existing LAN-IP fallback so a phone can reach the machine.
      if (!pageHostIsReachable && qrInfo?.lan_accessible === false) {
        canvas.textContent = "手机入口尚未启用";
        urlEl.textContent = qr.buildMobileWebUrl({
          scheme: window.location.protocol === "https:" ? "https" : "http",
          host: def.host,
          port: def.port,
        });
        hintEl.textContent = "当前后端只监听 127.0.0.1，因此手机无法连接。需要先把正式启动监听地址改为 0.0.0.0 并重启；在此之前不会生成或复制一个看似可用、实际打不开的手机链接。";
        hintEl.hidden = false;
        return;
      }
      const host = pageHostIsReachable ? def.host : (lanIp || typedHost || def.host);
      const port = pageHostIsReachable ? def.port : (typedPort || def.port);
      const scheme = window.location.protocol === "https:" ? "https" : "http";
      const url = qr.buildMobileWebUrl({ scheme, host, port });
      urlEl.textContent = url;
      if (qr.isLoopbackMobileHost(host)) {
        hintEl.textContent =
          "没拿到局域网 IP（后端可能只监听了本机地址）。手机打不开本机地址：请用 --host 0.0.0.0 启动后端，或手动把地址里的 127.0.0.1 换成电脑的局域网 IP。";
        hintEl.hidden = false;
        canvas.textContent = "手机入口尚未启用";
        return;
      }
      try {
        canvas.innerHTML = qr.createQrSvgMarkup(url);
        if (copyButton) copyButton.disabled = false;
      } catch {
        canvas.textContent = "二维码生成失败，请直接复制上方链接。";
        if (copyButton) copyButton.disabled = false;
      }
    }
    safeBind("#mobileQrBtn", "click", () => { closeSideDrawer(); void openMobileQrDrawer(); });
    safeBind("#mobileQrCalloutOpen", "click", () => { closeSideDrawer(); void openMobileQrDrawer(); });
    safeBind("#mobileQrCalloutClose", "click", markMobileQrSeen);
    initMobileQrDiscovery();
    safeBind("#mobileQrCopyBtn", "click", async () => {
      const url = $("#mobileQrUrl")?.textContent || "";
      if (!url.startsWith("http")) return;
      try {
        await navigator.clipboard.writeText(url);
        showToast("手机版链接已复制");
      } catch {
        showToast("复制失败，请手动选中链接复制");
      }
    });
    safeBind("#profileBtn", "click", () => openProfilePage());
    safeBind("#homeBtn", "click", () => openHomePage("events"));
    safeBind("#newMaterialsBtn", "click", openNewMaterialsPage);
    safeBind("#contentLibraryBtn", "click", () => openContentLibraryPage());
    bindDesktopContentLibrary();
    bindPersonalMemory();
    safeBind("#historyRefreshBtn", "click", () => refreshContentHistory(true));
    safeBind("#profileMemoryMoreBtn", "click", loadMoreProfileMemory);
    safeBind("#chatBtn", "click", openChatPage);
    safeBind("#messagesBtn", "click", () => {
      closeSideDrawer();
      hydrateInboxFromSpeculations(state.profile?.speculative_interests);
      hydrateInboxFromSpeculations(state.profile?.speculative_avoidances, "avoidance.probe");
      state.messageListSnapshot = getRenderableMessages();
      openPanel("messagesDrawer");
      returnToMessages();
      renderMessages();
      void refreshProfile().catch(() => {});
    });
    safeBind("#activityBtn", "click", () => { closeSideDrawer(); renderActivityHistory(); openPanel("activityDrawer"); });
    safeBind("#activityMoreBtn", "click", () => loadActivityPage());
    safeBind("#settingsBtn", "click", () => openSettingsPage("models"));
    safeBind("#openSettingsHero", "click", () => openSettingsPage("models"));
    bindAboutButton();
    syncTopbarHeight();
    window.addEventListener("resize", syncTopbarHeight);
    safeBind("#themeToggleBtn", "click", cycleThemeMode);
    bindRovingChoiceGroup("[data-theme-choice]", (button) => setThemeMode(button.dataset.themeChoice, { toast: true }));
    bindRovingChoiceGroup("[data-hue]", (button) => setThemeHue(parseInt(button.dataset.hue, 10), { toast: true }));
    bindRovingChoiceGroup("[data-accent-choice]", (button) => setAccentStyle(button.dataset.accentChoice, { toast: true }));
    safeBind("#hueSlider", "input", (event) => {
      const val = parseInt(event.target.value, 10);
      setThemeHue(val);
    });
    safeBind("#hueValueInput", "change", (event) => {
      const val = Math.min(360, Math.max(0, parseInt(event.target.value, 10) || 0));
      setThemeHue(val);
    });
    safeBind("#autoLoadOnScrollSetting", "change", (event) => {
      setAutoLoadOnScroll(Boolean(event.target.checked), { toast: true });
    });
    grid?.addEventListener("scroll", () => {
      const gate = nextRecommendationGridScrollGate(grid, {
        intent: recommendationGridScrollIntent,
        armed: recommendationGridBottomArmed,
      });
      recommendationGridScrollIntent = gate.intent;
      recommendationGridBottomArmed = gate.armed;
      scheduleAutoLoadCheck();
    }, { passive: true });
    window.addEventListener("resize", scheduleAutoLoadCheck);
    safeBind("#workbenchQueueTab", "click", () => setWorkbenchView("recommendations"));
    safeBind("#workbenchDelightTab", "click", () => setWorkbenchView("delight"));
    ["#workbenchQueueTab", "#workbenchDelightTab"].forEach((selector) => {
      safeBind(selector, "keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const delight = event.key === "ArrowRight" || event.key === "End";
        setWorkbenchView(delight ? "delight" : "recommendations", { focus: true });
      });
    });
    safeBind("#recommendationDetailScrim", "click", () => closeRecommendationDetailSheet());
    window.addEventListener("resize", () => {
      syncRecommendationSelection();
      syncRecommendationDetailSheetPresentation();
    });
    document.addEventListener("keydown", (event) => {
      if (!isNarrowRecommendationWorkbench() || !state.recommendationDetailSheetOpen) return;
      const pane = $("#recommendationDetailPane");
      if (!pane) return;
      if (event.key === "Escape") {
        event.preventDefault();
        closeRecommendationDetailSheet();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(pane.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'))
        .filter((element) => !element.hidden && element.getClientRects().length > 0);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
    safeBind("#reshuffleBtn", "click", reshuffle);
    safeBind("#loadMoreBtn", "click", appendMore);
    ensureDelightThumbAnchor();
    safeBind("#delightThumb", "click", (event) => {
      if (delightFeedbackInFlight || !delightContentUrl(state.delight)) event.preventDefault();
      void respondDelight(state.delight, "view");
    });
    safeBind("#delightThumb", "auxclick", (event) => {
      if (event.button !== 1) return;
      if (delightFeedbackInFlight || !delightContentUrl(state.delight)) event.preventDefault();
      void respondDelight(state.delight, "view");
    });
    safeBind("#delightThumb", "keydown", (event) => {
      if (event.currentTarget?.getAttribute("href")) return;
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      respondDelight(state.delight, "view");
    });
    safeBind("#delightCommentInput", "keydown", (event) => {
      if (event.key === "Enter") respondDelight(state.delight, "send-comment");
      if (event.key === "Escape") closeDelightComposer();
    });
    safeBind("#delightCommentInput", "blur", (event) => {
      autoCollapseComposer(document.querySelector(".delight-main-actions"), event, closeDelightComposer);
    });
    safeBind("#resetFiltersBtn", "click", () => {
      state.query = "";
      state.filter = "全部";
      state.mediaFilter = ALL_MEDIA_FILTER;
      const input = $("#searchInput");
      if (input) input.value = "";
      renderAll();
      $('#filterRow .chip[data-filter="全部"]')?.focus({ preventScroll: true });
    });
    safeBind("#mediaFilterSelect", "change", (event) => {
      state.mediaFilter = event.target.value || ALL_MEDIA_FILTER;
      renderAll();
      $("#mediaFilterSelect")?.focus({ preventScroll: true });
    });
    safeBind("#searchInput", "input", (event) => applyHomeSearch(event.target.value));
    safeBind("#searchForm", "submit", (event) => { event.preventDefault(); applyHomeSearch($("#searchInput")?.value || ""); });
    window.addEventListener("resize", scheduleActivityRailHeightSync);
    safeBind("#desktopPendingToggle", "click", () => {
      state.pendingConfirmations.expanded = !state.pendingConfirmations.expanded;
      renderDesktopPendingConfirmations();
      if (state.pendingConfirmations.expanded) void refreshDesktopPendingConfirmations();
    });
    $("#desktopPendingConfirmations")?.addEventListener("click", (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("[data-confirmation-ref]")
        : null;
      if (button instanceof HTMLButtonElement) void handleDesktopPendingOpen(button);
    });
    $("#chatLog")?.addEventListener("click", (event) => {
      activateReplyQuote(event, $("#chatLog"));
      const button = event.target instanceof Element
        ? event.target.closest("[data-card-action]")
        : null;
      if (button instanceof HTMLButtonElement) void handleDesktopCardAction(button);
    });
    safeBind("#chatForm", "submit", (event) => { event.preventDefault(); const input = $("#chatInput"); const text = input?.value?.trim() || ""; if (!text) return; input.value = ""; sendChat(text); });
    safeBind("#messageChatBackBtn", "click", returnToMessages);
    safeBind("#messageChatForm", "submit", (event) => {
      event.preventDefault();
      const input = $("#messageChatInput");
      const text = input?.value?.trim() || "";
      if (!text) return;
      input.value = "";
      if (state.messageChatDomain && (state.messageChatScope === "probe" || state.messageChatScope === "avoidance_probe")) {
        const probeType = state.messageChatScope === "avoidance_probe" ? "avoidance.probe" : "interest.probe";
        state.handledProbeKeys.add(probeKey(probeType, state.messageChatDomain));
      }
      sendChat(text, {
        contextPrefix: state.messageChatPrompt,
        scope: state.messageChatScope,
        subjectId: state.messageChatDomain,
        subjectTitle: state.messageChatSubjectTitle
      });
    });
    safeBind("#addLlmInstance", "click", () => openLlmInstanceDialog());
    safeBind("#closeLlmInstanceDialog", "click", closeLlmInstanceDialog);
    safeBind("#cancelLlmInstance", "click", closeLlmInstanceDialog);
    safeBind("[data-close-llm-instance-dialog]", "click", closeLlmInstanceDialog);
    safeBind("#saveLlmInstance", "click", saveLlmInstanceDraft);
    safeBind("#llmInstanceProviderType", "change", applyLlmProviderDefaults);
    safeBind("#refreshLlmInstanceModels", "click", () => { void discoverLlmInstanceModels(); });
    safeBind("#llmInstanceBaseUrl", "input", resetLlmModelDiscovery);
    safeBind("#llmInstanceApiKey", "input", resetLlmModelDiscovery);
    safeBind("#llmInstanceAuthMode", "change", resetLlmModelDiscovery);
    safeBind("#addLlmDefaultChainItem", "click", () => addLlmChainItem("default"));
    safeBind("#probeLlmChain", "click", () => { void runLlmChainProbe(); });
    for (const [moduleName, ui] of Object.entries(LLM_MODULE_UI)) {
      safeBind(`#${ui.mode}`, "change", (event) => {
        if (!state.llmDraft) return;
        const route = state.llmDraft.routes[moduleName];
        route.inherit = event.currentTarget.value !== "custom";
        if (!route.inherit && !route.chain.length) route.chain = [...state.llmDraft.default_chain];
        renderLlmRouting();
      });
      safeBind(`#${ui.add}`, "click", () => addLlmChainItem(moduleName));
    }
    document.addEventListener("keydown", (event) => {
      const dialog = $("#llmInstanceDialog");
      if (!dialog || dialog.hidden) return;
      if (event.key === "Escape") {
        event.preventDefault();
        closeLlmInstanceDialog();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialog.querySelectorAll("button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex='0']")).filter((element) => !element.closest("[hidden]"));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
    safeBind("#probeEmbedding", "click", () => { void runEmbeddingConfigProbe(); });
    safeBind("#probeNetworkProxy", "click", () => { void runNetworkProxyConfigProbe(); });
    lanAuthControl = initLanAuthControl();
    bootAutostartControl = initBootAutostartControl();
    // ---- 设置页吸底保存栏：未保存修改计数 --------------------------------
    // Counts distinct touched fields, not events, so holding a key down or
    // retyping the same input does not inflate the number.
    const settingsDirtyFields = new Set();
    let settingsSaveInFlight = false;
    let settingsSavePhase = "idle";
    let settingsPendingApplyRevision = 0;
    let settingsLastTerminalRevision = 0;

    safeBind("#migrationExportBtn", "click", () => { void exportMigrationData(); });
    safeBind("#migrationImportBtn", "click", () => {
      const input = $("#migrationImportFile");
      if (!(input instanceof HTMLInputElement) || migrationBusy) return;
      input.value = "";
      input.click();
    });
    safeBind("#migrationImportFile", "change", (event) => {
      const input = event.currentTarget;
      const file = input instanceof HTMLInputElement ? input.files?.[0] : null;
      if (!file) return;
      void stageMigrationImport(file).finally(() => { input.value = ""; });
    });
    safeBind("#migrationCancelBtn", "click", () => { void cancelPendingMigration(); });

    function settingsFormHasActiveEditor() {
      const settingsForm = document.getElementById("settingsForm");
      const active = document.activeElement;
      return Boolean(
        settingsForm &&
        active instanceof Element &&
        settingsForm.contains(active) &&
        active.matches('input:not([readonly]), textarea:not([readonly]), select, [contenteditable="true"]')
      );
    }

    function scheduleSettingsHydrationIfSafe() {
      if (settingsDirtyFields.size > 0 || settingsFormHasActiveEditor()) return;
      scheduleBackendHydration();
    }

    function renderSettingsDirty() {
      const bar = $("#settingsSaveBar");
      const msg = $("#settingsSaveMsg");
      const discard = $("#settingsDiscardBtn");
      const save = $("#settingsSaveBtn");
      const count = settingsDirtyFields.size;
      const phase = settingsSaveInFlight
        ? "saving"
        : count > 0 ? "dirty" : settingsSavePhase;
      const messages = {
        idle: "没有未保存的修改",
        saving: "正在保存配置…",
        applying: "配置已保存，正在后台应用…",
        applied: "配置已应用",
        failed: "配置应用失败，已恢复上一次生效配置",
      };
      if (bar) {
        bar.dataset.dirty = count > 0 ? "true" : "false";
        bar.dataset.saveState = phase;
        bar.toggleAttribute("aria-busy", phase === "saving" || phase === "applying");
      }
      if (msg) {
        msg.textContent = phase === "dirty"
          ? `已修改 ${count} 项，未保存`
          : (messages[phase] || messages.idle);
      }
      if (discard) discard.disabled = settingsSaveInFlight || count === 0;
      if (save) save.disabled = settingsSaveInFlight || count === 0;
    }

    function applyConfigApplyStatus(snapshot, { source = "status" } = {}) {
      const applyState = String(snapshot?.state || "");
      const requested = Number(snapshot?.requested_revision || 0);
      const applied = Number(snapshot?.applied_revision || 0);
      const fromRuntimeEvent = source === "runtime-event";
      const terminalRevision = Math.max(requested, applied);
      let reachedTerminal = false;
      if (
        settingsPendingApplyRevision > 0 &&
        requested < settingsPendingApplyRevision
      ) return false;
      if (
        terminalRevision > 0 &&
        ["applied", "failed"].includes(applyState) &&
        terminalRevision <= settingsLastTerminalRevision
      ) return false;
      if (
        settingsPendingApplyRevision === 0 &&
        ["queued", "applying"].includes(applyState) &&
        settingsLastTerminalRevision > 0 &&
        requested <= settingsLastTerminalRevision
      ) return false;
      if (
        settingsPendingApplyRevision === 0 &&
        ["applied", "failed"].includes(applyState) &&
        !fromRuntimeEvent
      ) return false;

      if (["queued", "applying"].includes(applyState)) {
        settingsPendingApplyRevision = Math.max(settingsPendingApplyRevision, requested);
        settingsSavePhase = "applying";
      } else if (
        applyState === "applied" &&
        (
          (settingsPendingApplyRevision > 0 && applied >= settingsPendingApplyRevision) ||
          (fromRuntimeEvent && requested > 0)
        )
      ) {
        settingsPendingApplyRevision = 0;
        settingsSavePhase = "applied";
        settingsLastTerminalRevision = Math.max(settingsLastTerminalRevision, terminalRevision);
        reachedTerminal = true;
      } else if (
        applyState === "failed" &&
        (
          (settingsPendingApplyRevision > 0 && requested >= settingsPendingApplyRevision) ||
          (fromRuntimeEvent && requested > 0)
        )
      ) {
        settingsPendingApplyRevision = 0;
        settingsSavePhase = "failed";
        settingsLastTerminalRevision = Math.max(settingsLastTerminalRevision, terminalRevision);
        reachedTerminal = true;
      }
      renderSettingsDirty();
      if (reachedTerminal) {
        if (settingsSavePhase === "failed" && settingsDirtyFields.size > 0) {
          // Keep a new local draft in the form, but refresh the canonical
          // snapshot used by Discard. Otherwise a failed save followed by a
          // second edit would make Discard restore the rejected candidate.
          void refreshConfigSnapshotOnly();
        } else {
          scheduleSettingsHydrationIfSafe();
        }
      }
      return true;
    }

    async function refreshConfigSnapshotOnly() {
      const snapshot = await requestJson(ENDPOINTS.config, { cache: "no-store" });
      const config = snapshot?.config || snapshot;
      if (config && typeof config === "object") state.config = config;
    }

    async function refreshConfigApplyStatus() {
      const snapshot = await requestJson(ENDPOINTS.configApplyStatus, { cache: "no-store" });
      if (snapshot) applyConfigApplyStatus(snapshot);
    }

    function markSettingsDirty(target) {
      const el = target instanceof Element ? target : null;
      settingsDirtyFields.add(el?.id || el?.name || `anon:${settingsDirtyFields.size}`);
      renderSettingsDirty();
    }

    function clearSettingsDirty() {
      settingsDirtyFields.clear();
      renderSettingsDirty();
    }

    ["input", "change"].forEach((type) => {
      $("#settingsForm")?.addEventListener(type, (event) => {
        const el = event.target;
        if (!(el instanceof Element)) return;
        // Read-only status mirrors (配置状态 / 凭据脱敏预览) are written by the
        // page itself and must never look like a user edit.
        if (
          el.hasAttribute("readonly") ||
          el.hasAttribute("data-settings-ignore-dirty") ||
          el.classList.contains("source-credential-value")
        ) return;
        markSettingsDirty(el);
      });
    });

    safeBind("#settingsDiscardBtn", "click", async () => {
      if (state.config) {
        applyConfig(state.config);
        restoreFrontendSettings(state.config);
      }
      try {
        await refreshNewsSourceContracts({ throwOnError: true });
        clearSettingsDirty();
        showToast("已放弃未保存的修改");
      } catch (error) {
        showToast(`新闻源草稿恢复失败：${error?.message || "请检查本机后端"}`);
      }
    });

    safeBind("#settingsForm", "submit", async (event) => {
      event.preventDefault();
      const submitBtn = $("#settingsSaveBtn");
      if (settingsSaveInFlight || settingsDirtyFields.size === 0) {
        renderSettingsDirty();
        return;
      }
      const previousText = submitBtn?.textContent || "保存配置";
      settingsSaveInFlight = true;
      renderSettingsDirty();
      if (submitBtn) {
        submitBtn.textContent = "保存中…";
      }
      $("#configStatus")?.removeAttribute("role");
      const endpoint = persistBackendEndpoint();
      const frontend = persistFrontendSettings();
      if ($("#configStatus")) $("#configStatus").value = `正在保存到 ${endpoint.host}:${endpoint.port}，惊喜队列加载 ${frontend.delightQueueLimit} 条，主题${THEME_LABELS[frontend.themeMode]}，滚动自动加载${frontend.autoLoadOnScroll ? "已开启" : "已关闭"}，后端热重载可能需要几秒。`;
      try {
        await Promise.all([
          persistNewsSourceSettings(),
          persistNewsRuntimeSettings(),
        ]);
        const payload = buildConfigUpdate();
        const result = await requestJsonStrict(ENDPOINTS.config, {
          method: "PUT",
          timeoutMs: 60000,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        if (result?.config) applyConfig(result.config);
        else clearSettingsDirty();
        const message = result?.message || "配置已保存。";
        const queued = result?.apply_state === "queued";
        if (queued) {
          settingsPendingApplyRevision = Math.max(
            settingsPendingApplyRevision,
            Number(result?.apply_revision || 0),
          );
          settingsSavePhase = "applying";
          renderSettingsDirty();
        } else if (result?.reloaded === true) {
          settingsPendingApplyRevision = 0;
          settingsSavePhase = "applied";
          renderSettingsDirty();
        }
        const suffix = result?.restart_required
          ? "\n当前配置需要重启后端后完全生效。"
          : queued
            ? "\n配置已进入后台应用队列，连续保存会自动合并为最新版本。"
            : result?.reloaded === false ? "\n后端返回未热重载，请检查运行状态。" : "";
        if ($("#configStatus")) $("#configStatus").value = `${message}${suffix}`;
        showToast(
          result?.restart_required
            ? "配置已保存，需要重启后端"
            : queued ? "配置已保存，正在后台应用…" : "配置已保存"
        );
        if (queued) void refreshConfigApplyStatus();
      } catch (error) {
        if (error?.code === "request_timeout") {
          const pendingMessage = "保存请求超时，当前无法确认配置是否已经写入；请稍后查看配置状态，避免立即重复提交。";
          if ($("#configStatus")) {
            $("#configStatus").setAttribute("role", "status");
            $("#configStatus").value = pendingMessage;
          }
          showToast("保存请求超时，请稍后确认配置状态", { duration: 5200 });
          return;
        }
        const message = configErrorMessage(error.details) || error.message || "未知错误";
        if ($("#configStatus")) { $("#configStatus").setAttribute("role", "alert"); $("#configStatus").value = `保存失败：\n${message}`; }
        showToast("保存失败：请查看配置状态");
      } finally {
        settingsSaveInFlight = false;
        if (submitBtn) {
          submitBtn.textContent = previousText;
        }
        renderSettingsDirty();
      }
    });
    const delightBanner = $("#delightBanner");
    if (delightBanner) {
        delightBanner.addEventListener("mouseenter", _stopDelightAutoAdvance);
        delightBanner.addEventListener("mouseleave", _startDelightAutoAdvance);
        delightBanner.addEventListener("touchstart", _stopDelightAutoAdvance, { passive: true });
        delightBanner.addEventListener("touchend", _startDelightAutoAdvance, { passive: true });
    }
    document.querySelectorAll("[data-delight]").forEach((btn) => btn.addEventListener("click", async () => {
      const response = btn.dataset.delight;
        if (response === "prev") { setActiveDelight(state.delightIndex <= 0 ? state.delights.length - 1 : state.delightIndex - 1); return; }
        if (response === "next") { setActiveDelight(state.delightIndex >= state.delights.length - 1 ? 0 : state.delightIndex + 1); return; }
      // 「阅读原文」是纯按钮（不像封面 <a> 能原生导航），必须由 JS 打开内容。
      await respondDelight(state.delight, response, null, response === "view");
    }));
    $("#delightExcerptToggle")?.addEventListener("click", () => {
      const wrapper = $("#delightExcerpt");
      const toggle = $("#delightExcerptToggle");
      if (!wrapper || !toggle) return;
      const expanded = wrapper.classList.toggle("is-expanded");
      const excerpt = $("#delightExcerptText");
      if (excerpt) excerpt.hidden = !expanded;
      toggle.textContent = expanded ? "收起原始内容" : "查看已采集的原始内容";
      toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
      scheduleActivityRailHeightSync();
    });

    function formatNewsSourceTime(value) {
      const parsed = Date.parse(String(value || ""));
      return Number.isFinite(parsed) ? new Date(parsed).toLocaleString() : "尚无成功记录";
    }

    let newsSourceContractItems = [];
    let newsSourceOriginalEnabled = new Map();

    function newsSourceId(item) {
      return String(item?.source_id || item?.id || item?.key || item?.source_key || "");
    }

    function newsSourceControlValues(groupKey) {
      const prefix = ({
        tushare_news: "newsTushareNews",
        tushare_major_news: "newsTushareMajor",
        tushare_cctv_news: "newsTushareCctv",
      })[groupKey];
      if (!prefix) return null;
      return {
        enabled: document.getElementById(`${prefix}Enabled`)?.value === "on",
      };
    }

    function newsSourceEnabledValue(item) {
      const sourceId = newsSourceId(item);
      const dynamicControl = Array.from(document.querySelectorAll("[data-news-source-enabled]"))
        .find((control) => control.dataset.newsSourceEnabled === sourceId);
      if (dynamicControl instanceof HTMLSelectElement) return dynamicControl.value === "on";
      return newsSourceControlValues(newsSourceGroup(item))?.enabled;
    }

    async function persistNewsRuntimeSettings() {
      const payload = {
        pool_target_count: Math.max(20, getIntInput("poolTarget", 300)),
        refresh_poll_minutes: Math.max(5, getIntInput("refreshCheckInterval", 10)),
        hot_refresh_minutes: Math.max(5, getIntInput("trendingRefreshMinutes", 5)),
        exploration_refresh_minutes: Math.max(15, getIntInput("exploreRefreshMinutes", 15)),
        feedback_threshold: Math.max(1, getIntInput("feedbackBatchThreshold", 3)),
        proactive_push: getIntInput("proactivePushInterval", 120) > 0,
        background_llm: document.getElementById("schedulerEnabled")?.value !== "off",
        browser_close_behavior: document.getElementById("pauseDisconnect")?.value === "pause" ? "pause" : "continue",
      };
      try {
        return await requestJsonStrict("/news/settings", {
          method: "PUT",
          timeoutMs: 20000,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } catch (error) {
        if (error?.status === 404) return null;
        throw error;
      }
    }

    async function persistNewsSourceSettings() {
      if (!newsSourceContractItems.length) {
        try {
          const snapshot = await requestJsonStrict("/news/sources", { timeoutMs: 15000 });
          renderNewsSourceContracts(snapshot);
        } catch (error) {
          if (error?.status === 404) return null;
          throw error;
        }
      }

      const existingCustom = newsSourceContractItems.filter((item) => newsSourceGroup(item) === "custom_rss");
      const desiredUrls = getInput("newsCustomRssUrls").split(/\r?\n/).map((url) => url.trim()).filter(Boolean);
      const normalizedDesired = new Map();
      for (const rawUrl of desiredUrls) {
        let url;
        try { url = new URL(rawUrl).toString(); }
        catch { throw new Error(`自定义 RSS 地址无效：${rawUrl}`); }
        normalizedDesired.set(url, rawUrl);
      }
      const existingByUrl = new Map(existingCustom.map((item) => {
        try { return [new URL(String(item?.url || "")).toString(), item]; }
        catch { return [String(item?.url || ""), item]; }
      }));

      const updates = [];
      for (const item of newsSourceContractItems) {
        const sourceId = newsSourceId(item);
        if (!sourceId) continue;
        if (newsSourceGroup(item) === "custom_rss") {
          let normalizedUrl = String(item?.url || "");
          try { normalizedUrl = new URL(normalizedUrl).toString(); } catch {}
          if (!normalizedDesired.has(normalizedUrl)) continue;
        }
        const enabled = newsSourceEnabledValue(item);
        if (typeof enabled !== "boolean" || enabled === newsSourceOriginalEnabled.get(sourceId)) continue;
        updates.push(requestJsonStrict(`/news/sources/${encodeURIComponent(sourceId)}`, {
          method: "PUT",
          timeoutMs: 20000,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled }),
        }));
      }
      for (const [url, item] of existingByUrl) {
        const sourceId = newsSourceId(item);
        if (!sourceId) continue;
        if (!normalizedDesired.has(url)) {
          updates.push(requestJsonStrict(`/news/sources/${encodeURIComponent(sourceId)}`, { method: "DELETE", timeoutMs: 20000 }));
        }
      }
      for (const [url] of normalizedDesired) {
        if (existingByUrl.has(url)) continue;
        const hostname = new URL(url).hostname.replace(/^www\./, "");
        updates.push(requestJsonStrict("/news/sources/rss", {
          method: "POST",
          timeoutMs: 20000,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            media: hostname || "自定义 RSS",
            url,
            enabled: true,
            weight: 1,
            refresh_minutes: 60,
          }),
        }));
      }
      await Promise.all(updates);
      return refreshNewsSourceContracts({ throwOnError: true });
    }

    function newsSourceGroup(item) {
      const id = newsSourceId(item);
      if (id === "tushare:news") return "tushare_news";
      if (id === "tushare:major_news") return "tushare_major_news";
      if (id === "tushare:cctv_news") return "tushare_cctv_news";
      if (item?.kind === "rss" || id.startsWith("rss:")) return item?.custom ? "custom_rss" : "official_rss";
      return id;
    }

    function newsSourceHealth(item) {
      if (item?.credential_configured === false) return "no_permission";
      return String(
        (item?.health && typeof item.health === "object" ? item.health.status : item?.health)
        || item?.state || item?.status || "unverified"
      ).toLowerCase();
    }

    function updateNewsSourceCard(card, group) {
      if (!card || !group.length) return;
      card.dataset.newsSourceIds = group.map(newsSourceId).filter(Boolean).join("|");
      const healths = group.map(newsSourceHealth);
      const healthy = healths.filter((value) => ["ok", "ready", "healthy"].includes(value)).length;
      const health = healthy === group.length ? "ok" : healthy > 0 ? "degraded" : healths[0];
      card.dataset.newsHealth = health;
      const badge = card.querySelector("[data-news-health]");
      const detail = card.querySelector("[data-news-detail]");
      const success = card.querySelector("[data-news-last-success]");
      if (badge) badge.textContent = ({
        ok: "可用", ready: "可用", healthy: "可用", degraded: "降级",
        unavailable: "不可用", timeout: "超时", rate_limited: "频率受限",
        no_permission: "无接口权限", error: "失败", empty: "本轮无数据",
      })[health] || "待验证";
      const errors = group.map((item) => item?.error || item?.health?.error_message || "").filter(Boolean);
      if (detail) {
        if (!detail.dataset.newsDefaultDetail) detail.dataset.newsDefaultDetail = detail.textContent || "";
        detail.textContent = errors.length ? errors.slice(0, 2).join("；") : detail.dataset.newsDefaultDetail;
      }
      const lastSuccess = group.map((item) => item?.last_success_at || item?.last_success || item?.health?.last_success_at || "")
        .filter(Boolean).sort((left, right) => Date.parse(right) - Date.parse(left))[0];
      if (success) success.textContent = `最后成功：${formatNewsSourceTime(lastSuccess)}`;
    }

    function renderNewsRssSourceCards(items) {
      const container = document.getElementById("newsRssSourceItems");
      if (!container) return;
      const pendingEnabled = new Map();
      container.querySelectorAll("[data-news-source-enabled]").forEach((control) => {
        if (settingsDirtyFields.has(control.id)) {
          pendingEnabled.set(control.dataset.newsSourceEnabled, control.value);
        }
      });
      container.replaceChildren();
      items.filter((item) => newsSourceGroup(item) === "official_rss" || newsSourceGroup(item) === "custom_rss")
        .forEach((item, index) => {
          const sourceId = newsSourceId(item);
          if (!sourceId) return;
          const custom = newsSourceGroup(item) === "custom_rss";
          const card = document.createElement("article");
          card.className = "source-card news-source-card";
          card.dataset.newsSourceId = sourceId;
          card.innerHTML = `
            <div class="source-card-face">
              <span class="source-card-logo" aria-hidden="true"></span>
              <span class="source-card-head">
                <strong class="source-card-name"></strong>
                <span class="source-status-badges"><span class="source-source-badge"></span><span class="source-access-badge" data-news-health>待验证</span></span>
                <p class="src-detail" data-news-detail></p>
                <span class="source-card-hint-slot" data-news-last-success>尚无成功记录</span>
              </span>
              <span class="source-card-right"><label class="source-card-enable"><span class="sr-only"></span><select data-news-source-enabled><option value="on">启用</option><option value="off">停用</option></select></label></span>
            </div>`;
          card.querySelector(".source-card-logo").textContent = custom ? "+" : "订";
          const media = String(item?.media || item?.name || sourceId);
          card.querySelector(".source-card-name").textContent = media;
          card.querySelector(".source-source-badge").textContent = `提供方：${String(item?.provider || item?.technical_provider || (custom ? "自定义 RSS/Atom" : "官方 RSS/Atom"))}`;
          card.querySelector("[data-news-detail]").textContent = item?.url
            ? `${custom ? "自定义订阅" : "媒体官方订阅"}：${String(item.url)}`
            : (custom ? "自定义订阅地址" : "媒体官方订阅源");
          const select = card.querySelector("[data-news-source-enabled]");
          select.id = `newsRssEnabled${index}`;
          select.dataset.newsSourceEnabled = sourceId;
          select.value = pendingEnabled.get(sourceId) || (item?.enabled === false ? "off" : "on");
          select.previousElementSibling.textContent = `${media}启用状态`;
          container.append(card);
          updateNewsSourceCard(card, [item]);
        });
    }

    function applyNewsSourceControls(groupKey, items) {
      const prefix = ({
        tushare_news: "newsTushareNews",
        tushare_major_news: "newsTushareMajor",
        tushare_cctv_news: "newsTushareCctv",
      })[groupKey];
      if (!items.length) return;
      const primary = items[0];
      const setIfClean = (id, value) => {
        const input = document.getElementById(id);
        if (!input || settingsDirtyFields.has(id) || value === undefined || value === null || value === "") return;
        input.value = String(value);
      };
      if (prefix) setIfClean(`${prefix}Enabled`, primary.enabled !== false ? "on" : "off");
      if (groupKey === "custom_rss") {
        const urls = items.map((item) => String(item?.url || "").trim()).filter(Boolean);
        setIfClean("newsCustomRssUrls", urls.join("\n"));
      }
    }

    function applyNewsRuntimeSettings(settings) {
      if (!settings || typeof settings !== "object") return;
      const setIfClean = (id, value) => {
        const input = document.getElementById(id);
        if (!input || settingsDirtyFields.has(id) || value === undefined || value === null || value === "") return;
        input.value = String(value);
      };
      setIfClean("poolTarget", settings.pool_target_count);
      setIfClean("refreshCheckInterval", settings.refresh_poll_minutes);
      setIfClean("trendingRefreshMinutes", settings.hot_refresh_minutes);
      setIfClean("exploreRefreshMinutes", settings.exploration_refresh_minutes);
      setIfClean("feedbackBatchThreshold", settings.feedback_threshold);
      if (settings.background_llm != null) setIfClean("schedulerEnabled", settings.background_llm ? "on" : "off");
      if (settings.browser_close_behavior) setIfClean("pauseDisconnect", settings.browser_close_behavior === "pause" ? "pause" : "keep");
    }

    function renderNewsSourceContracts(payload) {
      const items = Array.isArray(payload) ? payload : (payload?.items || payload?.sources || []);
      applyNewsRuntimeSettings(payload?.settings);
      newsSourceContractItems = items;
      newsSourceOriginalEnabled = new Map(items.map((item) => [newsSourceId(item), item?.enabled !== false]));
      const groups = new Map();
      items.forEach((item) => {
        const key = newsSourceGroup(item);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(item);
      });
      renderNewsRssSourceCards(items);
      document.querySelectorAll("[data-news-source]").forEach((card) => {
        const group = groups.get(card.dataset.newsSource) || [];
        if (!group.length) return;
        updateNewsSourceCard(card, group);
        applyNewsSourceControls(card.dataset.newsSource, group);
      });
      applyNewsSourceControls("custom_rss", groups.get("custom_rss") || []);
    }

    async function refreshNewsSourceContracts({ announce = false, throwOnError = false } = {}) {
      const status = $("#newsSourceActionStatus");
      if (announce && status) status.textContent = "正在读取新闻源状态…";
      try {
        const payload = await requestJsonStrict("/news/sources", { timeoutMs: 15000 });
        renderNewsSourceContracts(payload);
        if (announce && status) status.textContent = "新闻源状态已更新。";
        return payload;
      } catch (error) {
        const reason = error?.message || "请检查本机后端";
        if (status) status.textContent = `新闻源状态读取失败：${reason}`;
        document.querySelectorAll("[data-news-source], [data-news-source-id]").forEach((card) => {
          card.dataset.newsHealth = "degraded";
          const badge = card.querySelector("[data-news-health]");
          const detail = card.querySelector("[data-news-detail]");
          if (badge) badge.textContent = "降级";
          if (detail) detail.textContent = `暂时无法读取该来源状态：${reason}`;
        });
        if (throwOnError) throw error;
        return null;
      }
    }

    async function refreshTushareCredentialStatus() {
      const status = $("#newsTushareTokenStatus");
      if (!status) return null;
      try {
        const payload = await requestJsonStrict("/news/credentials/tushare", { timeoutMs: 15000 });
        status.textContent = payload?.configured
          ? `已配置${payload?.stored_locally ? "（新闻爪本机凭据）" : "（启动环境）"}`
          : "尚未配置";
        status.dataset.tone = payload?.configured ? "success" : "";
        return payload;
      } catch (error) {
        status.textContent = `读取失败：${error?.message || "请检查本机后端"}`;
        status.dataset.tone = "error";
        return null;
      }
    }

    async function saveTushareCredential() {
      const input = $("#newsTushareToken");
      const button = $("#newsSaveTushareTokenBtn");
      const status = $("#newsTushareTokenStatus");
      const token = String(input?.value || "").trim();
      if (!token) {
        if (status) status.textContent = "没有输入新 Token，现有凭据未改变。";
        return;
      }
      if (button) button.disabled = true;
      if (status) status.textContent = "正在安全保存…";
      try {
        await requestJsonStrict("/news/credentials/tushare", {
          method: "PUT",
          timeoutMs: 20000,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token }),
        });
        input.value = "";
        settingsDirtyFields.delete(input.id);
        renderSettingsDirty();
        if (status) status.textContent = "已保存到新闻爪本机凭据；下次验证或刷新立即使用。";
        await refreshNewsSourceContracts();
      } catch (error) {
        if (status) status.textContent = `保存失败：${error?.message || "请检查 Token 格式"}`;
      } finally {
        if (button) button.disabled = false;
      }
    }

    async function refreshTavilyCredentialStatus() {
      const status = $("#newsTavilyApiKeyStatus");
      if (!status) return null;
      try {
        const payload = await requestJsonStrict("/research/providers/tavily", { timeoutMs: 15000 });
        status.textContent = payload?.configured
          ? `已配置${payload?.stored_locally ? "（新闻爪本机凭据）" : "（启动环境）"}`
          : "尚未配置；按需研究不可用";
        status.dataset.tone = payload?.configured ? "success" : "";
        return payload;
      } catch (error) {
        status.textContent = `读取失败：${error?.message || "请检查本机后端"}`;
        status.dataset.tone = "error";
        return null;
      }
    }

    async function saveTavilyCredential() {
      const input = $("#newsTavilyApiKey");
      const button = $("#newsSaveTavilyApiKeyBtn");
      const status = $("#newsTavilyApiKeyStatus");
      const apiKey = String(input?.value || "").trim();
      if (!apiKey) {
        if (status) status.textContent = "没有输入新 API Key，现有凭据未改变。";
        return;
      }
      if (button) button.disabled = true;
      if (status) status.textContent = "正在安全保存…";
      try {
        await requestJsonStrict("/research/providers/tavily/credential", {
          method: "PUT",
          timeoutMs: 20000,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ api_key: apiKey }),
        });
        input.value = "";
        settingsDirtyFields.delete(input.id);
        renderSettingsDirty();
        if (status) status.textContent = "已保存到新闻爪本机；页面不会回显原值。";
        await refreshTavilyCredentialStatus();
      } catch (error) {
        if (status) status.textContent = `保存失败：${error?.message || "请检查 API Key 格式"}`;
      } finally {
        if (button) button.disabled = false;
      }
    }

    async function deleteTavilyCredential() {
      const button = $("#newsDeleteTavilyApiKeyBtn");
      const status = $("#newsTavilyApiKeyStatus");
      if (button) button.disabled = true;
      if (status) status.textContent = "正在删除本机 Key…";
      try {
        const payload = await requestJsonStrict("/research/providers/tavily/credential", {
          method: "DELETE",
          timeoutMs: 20000,
        });
        if (status) {
          status.textContent = payload?.configured
            ? "本机 Key 已删除，启动环境中的 Key 仍在生效。"
            : "本机 Key 已删除，按需研究已停用。";
        }
        await refreshTavilyCredentialStatus();
      } catch (error) {
        if (status) status.textContent = `删除失败：${error?.message || "请检查本机后端"}`;
      } finally {
        if (button) button.disabled = false;
      }
    }

    async function probeTavilyProvider() {
      const button = $("#newsProbeTavilyBtn");
      const status = $("#newsTavilyApiKeyStatus");
      if (button) button.disabled = true;
      if (status) status.textContent = "正在通过 Tavily 用量接口验证；不会发起搜索…";
      try {
        const payload = await requestJsonStrict("/research/providers/tavily/probe", {
          method: "POST",
          timeoutMs: 30000,
        });
        if (status) {
          const usage = Number.isFinite(Number(payload?.key_usage)) && Number.isFinite(Number(payload?.key_limit))
            ? ` Key 用量 ${payload.key_usage}/${payload.key_limit}。`
            : "";
          status.textContent = payload?.ok ? `Tavily 连接正常。${usage}` : "Tavily 未返回可用状态。";
        }
      } catch (error) {
        if (status) status.textContent = `验证失败：${error?.message || "请检查 Key 或额度"}`;
      } finally {
        if (button) button.disabled = false;
      }
    }

    async function runNewsRefresh(mode) {
      const status = $("#newsSourceActionStatus");
      const validateButton = $("#newsValidateSourcesBtn");
      const refreshButton = $("#newsRefreshNowBtn");
      const activeButton = mode === "validate" ? validateButton : refreshButton;
      if (activeButton) activeButton.disabled = true;
      if (status) status.textContent = mode === "validate" ? "正在逐项验证新闻源…" : "正在创建刷新任务…";
      try {
        const requestId = globalThis.crypto?.randomUUID?.() || `news-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        let result;
        if (mode === "validate") {
          try {
            result = await requestJsonStrict("/news/sources/probe", {
              method: "POST",
              timeoutMs: 60000,
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ source_ids: [] })
            });
          } catch (error) {
            if (error?.status !== 404) throw error;
          }
        }
        if (!result) {
          result = await requestJsonStrict("/news/refresh", {
            method: "POST",
            timeoutMs: mode === "validate" ? 60000 : 20000,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ request_id: requestId })
          });
        }
        if (status) status.textContent = String(result?.message || (mode === "validate" ? "验证完成。" : "刷新任务已创建。"));
        await refreshNewsSourceContracts();
      } catch (error) {
        if (status) status.textContent = `操作失败：${error?.message || "请稍后重试"}`;
      } finally {
        if (activeButton) activeButton.disabled = false;
      }
    }

    $("#newsValidateSourcesBtn")?.addEventListener("click", () => void runNewsRefresh("validate"));
    $("#newsRefreshNowBtn")?.addEventListener("click", () => void runNewsRefresh("refresh"));
    $("#newsSaveTushareTokenBtn")?.addEventListener("click", () => void saveTushareCredential());
    $("#newsSaveTavilyApiKeyBtn")?.addEventListener("click", () => void saveTavilyCredential());
    $("#newsDeleteTavilyApiKeyBtn")?.addEventListener("click", () => void deleteTavilyCredential());
    $("#newsProbeTavilyBtn")?.addEventListener("click", () => void probeTavilyProvider());
    restoreBackendEndpoint();
    restoreFrontendSettings();
    setSideDrawerOpen(!isMobileViewport() && storageGet(SIDE_DRAWER_OPEN_KEY) !== "0", { persist: false });
    initBackToTop();
    startSharedChatSurfaceSync();
    startChatPlaceholderRotation();
    toastManager.init();
    setupThemeNotice();
    setWorkbenchView(state.workbenchView);
    setHomeMode(state.homeMode, { persist: false });
    try {
      renderAll();
    } catch (error) {
      console.error("首屏渲染失败", error);
      $("#statusLabel").textContent = "首屏渲染失败";
      $("#runtimeSummary").textContent = error?.message || "请检查后端返回的数据结构。";
    }
    const requestedSettingsPanel = new URLSearchParams(window.location.search).get("settings");
    if (["models", "sources", "scheduler", "general", "frontend", "logging"].includes(requestedSettingsPanel)) {
      openSettingsPage(requestedSettingsPanel);
    }
    void startDesktopBackendSession({ forceHydrate: true });
    void refreshNewsSourceContracts();
    void refreshTushareCredentialStatus();
    void refreshTavilyCredentialStatus();
    })();
