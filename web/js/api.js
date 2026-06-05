/**
 * MediaLens — API Service Layer
 * 
 * 抽象 API 接口层。
 * 当前使用 mock 数据模拟后端响应，
 * Sprint 2 接入 FastAPI 后只需替换 BASE_URL 和对应实现。
 */

const API = {
  BASE_URL: 'http://localhost:8000/api', // FastAPI 后端地址

  /* ---- 模拟数据 ---- */
  _mock: {
    stats: {
      total_matches: 2847,
      cache_hits: 1952,
      llm_calls: 147,
      files_organized: 2623,
      cache_rate: 68.5,
      llm_rate: 5.2,
      recent_activity: 23,
    },

    recentMatches: [
      {
        id: 1,
        file: '功夫.2004.1080p.BluRay.x264.mkv',
        title: '功夫',
        original_title: 'Kung Fu Hustle',
        year: 2004,
        tmdb_id: 9470,
        confidence: 95,
        source: 'TMDB_EXACT',
        format: 'SINGLE_FILE',
        overview: '1940年代的上海，自小受尽欺辱的街头混混阿星为了能出人头地，不惜冒充斧头帮成员...',
        poster: null,
        created_at: '2026-06-05T14:30:00',
      },
      {
        id: 2,
        file: 'Inception.2010.IMAX.2160p.mkv',
        title: '盗梦空间',
        original_title: 'Inception',
        year: 2010,
        tmdb_id: 27205,
        confidence: 92,
        source: 'TMDB_EXACT',
        format: 'SINGLE_FILE',
        overview: '道姆·柯布是一位经验老道的窃贼——在人们精神最为脆弱的睡梦中，他能潜入别人的梦境中盗取潜意识中的秘密...',
        poster: null,
        created_at: '2026-06-05T13:15:00',
      },
      {
        id: 3,
        file: '望夫成龙/BDMV/',
        title: '望夫成龙',
        original_title: 'Love Is Love',
        year: 1990,
        tmdb_id: 64708,
        confidence: 78,
        source: 'LLM_VERIFIED',
        format: 'BLURAY_BDMV',
        overview: '石金水与吴带娣乡下长大，二人青梅竹马，私订终身，但为旱灾所迫，不得不双双到城市谋生...',
        poster: null,
        created_at: '2026-06-05T11:45:00',
      },
      {
        id: 4,
        file: 'Breaking.Bad.S01E01.1080p.mkv',
        title: '绝命毒师',
        original_title: 'Breaking Bad',
        year: 2008,
        tmdb_id: 1396,
        confidence: 99,
        source: 'CACHE_HIT',
        format: 'TV_EPISODE',
        overview: '新墨西哥州的高中化学老师沃尔特·H·怀特在得知自己身患绝症后，为了给家人留下财产...',
        poster: null,
        created_at: '2026-06-04T22:10:00',
        season: 1,
        episode: 1,
      },
    ],

    pendingFiles: [
      {
        name: '周星驰BD+DVD合集/BLURAY/望夫成龙/BDMV/',
        format: 'BLURAY_BDMV',
        size: '42.3 GB',
        status: 'pending',
      },
      {
        name: '周星驰BD+DVD合集/BLURAY/逃学威龙/BDMV/',
        format: 'BLURAY_BDMV',
        size: '38.7 GB',
        status: 'pending',
      },
      {
        name: 'The.Matrix.1999.REMUX.2160p.mkv',
        format: 'SINGLE_FILE',
        size: '56.2 GB',
        status: 'processing',
      },
      {
        name: 'Game.of.Thrones.S01.1080p.BluRay/',
        format: 'TV_SEASON',
        size: '128.5 GB',
        status: 'pending',
      },
    ],

    activityLog: [
      { time: '14:30:12', level: 'success', message: '✓ 功夫 (2004) → NFO 已生成 → 已完成整理' },
      { time: '14:28:05', level: 'info', message: 'ℹ 功夫.2004.1080p.BluRay.x264.mkv → TMDB 精确匹配 (置信度: 95%)' },
      { time: '14:25:33', level: 'info', message: 'ℹ 开始扫描目录: /media/downloads/周星驰合集/' },
      { time: '14:20:18', level: 'warn', message: '⚠ 望夫成龙/BDMV → TMDB 置信度 62% → 调用 LLM 补充裁决' },
      { time: '14:19:55', level: 'info', message: 'ℹ LLM 裁决完成 → 选择: 望夫成龙 (1990) [TMDB: 64708]' },
      { time: '14:15:42', level: 'success', message: '✓ Breaking.Bad.S01E01 → 缓存命中 → 直接返回' },
      { time: '14:12:08', level: 'error', message: '✗ unknown_file.xyz → 无法识别文件格式，已跳过' },
      { time: '14:10:00', level: 'info', message: 'ℹ MediaLens v0.1.0 启动完成，数据库已连接' },
    ],

    config: {
      tmdb: {
        api_key: 'sk-...abc123',
        language: 'zh-CN',
        confidence_threshold: 80,
      },
      llm: {
        provider: 'openai',
        model: 'gpt-4o-mini',
        api_key: 'sk-...xyz789',
        confidence_threshold: 60,
      },
      organize: {
        mode: 'hardlink',
        movie_template: '{title} ({year}) [{quality}]',
        tv_template: '{title}/Season {season:02d}/{title} - S{season:02d}E{episode:02d} - [{quality}]',
        strategy: 'year',
        dry_run: true,
      },
      db: {
        path: 'data/medialens.db',
      },
    },
  },

  /* ---- 请求方法 ---- */
  async _get(endpoint) {
    if (this.BASE_URL) {
      const res = await fetch(`${this.BASE_URL}${endpoint}`);
      if (!res.ok) throw new Error(`API error: ${res.status}`);
      return res.json();
    }
    // Mock fallback
    return this._mockResponse(endpoint);
  },

  async _post(endpoint, data) {
    if (this.BASE_URL) {
      const res = await fetch(`${this.BASE_URL}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error(`API error: ${res.status}`);
      return res.json();
    }
    return { success: true };
  },

  /* ---- Mock 路由 ---- */
  _mockResponse(endpoint) {
    if (endpoint === '/stats') return { ...this._mock.stats };
    if (endpoint === '/matches/recent') return [...this._mock.recentMatches];
    if (endpoint === '/files/pending') return [...this._mock.pendingFiles];
    if (endpoint === '/logs') return [...this._mock.activityLog];
    if (endpoint === '/config') return JSON.parse(JSON.stringify(this._mock.config));
    return {};
  },

  /* ---- 公开 API ---- */

  /** 获取仪表盘统计数据 */
  async getStats() {
    return this._get('/stats');
  },

  /** 获取最近的匹配记录 */
  async getRecentMatches(limit = 5) {
    const matches = await this._get('/matches/recent');
    return matches.slice(0, limit);
  },

  /** 获取待处理文件列表 */
  async getPendingFiles() {
    return this._get('/files/pending');
  },

  /** 获取活动日志 */
  async getLogs() {
    return this._get('/logs');
  },

  /** 获取配置 */
  async getConfig() {
    return this._get('/config');
  },

  /** 保存配置 */
  async saveConfig(config) {
    return this._post('/config', config);
  },

  /** 扫描文件 */
  async scanPath(path) {
    return this._post('/scan', { path });
  },

  /** 执行匹配 */
  async matchFile(path) {
    return this._post('/match', { path });
  },

  /** 执行整理 */
  async organize(data) {
    return this._post('/organize', data);
  },
};
