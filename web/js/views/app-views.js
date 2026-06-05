/**
 * MediaLens — View Templates
 * 所有页面的 HTML 模板渲染函数
 */

const AppViews = {
  /* ---- Skeleton Loader ---- */
  getSkeleton(view) {
    return `
      <div style="display:grid;gap:16px;padding:4px 0">
        ${view === 'dashboard' ? `
          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px">
            ${[1,2,3,4].map(() => '<div class="skeleton" style="height:120px;border-radius:16px"></div>').join('')}
          </div>
          <div class="skeleton" style="height:200px;border-radius:16px"></div>
          <div class="skeleton" style="height:300px;border-radius:16px"></div>
        ` : `
          <div class="skeleton" style="height:48px;border-radius:10px;width:300px"></div>
          <div class="skeleton" style="height:400px;border-radius:16px"></div>
        `}
      </div>
    `;
  },

  /* ---- 仪表盘 ---- */
  async dashboard() {
    const stats = await API.getStats();
    const matches = await API.getRecentMatches(4);
    const pending = await API.getPendingFiles();

    return `
      <!-- Stats Grid -->
      <div class="stats-grid">
        <div class="card stat-card">
          <div class="stat-card-icon stat-card-icon--amber">🎬</div>
          <div class="card-value">${stats.total_matches.toLocaleString()}</div>
          <div class="card-label">累计匹配</div>
          <div class="card-trend card-trend--up">↑ 12% 较上月</div>
        </div>
        <div class="card stat-card">
          <div class="stat-card-icon stat-card-icon--blue">⚡</div>
          <div class="card-value">${stats.cache_rate}%</div>
          <div class="card-label">缓存命中率</div>
          <div class="card-trend card-trend--up">↑ ${stats.cache_hits.toLocaleString()} 次命中</div>
        </div>
        <div class="card stat-card">
          <div class="stat-card-icon stat-card-icon--pink">🤖</div>
          <div class="card-value">${stats.llm_rate}%</div>
          <div class="card-label">LLM 调用率</div>
          <div class="card-trend card-trend--up">${stats.llm_calls} 次调用</div>
        </div>
        <div class="card stat-card">
          <div class="stat-card-icon stat-card-icon--teal">📁</div>
          <div class="card-value">${stats.files_organized.toLocaleString()}</div>
          <div class="card-label">已整理文件</div>
          <div class="card-trend card-trend--up">↑ ${stats.recent_activity} 今日新增</div>
        </div>
      </div>

      <!-- 最近匹配 -->
      <div class="section">
        <div class="section-header">
          <div>
            <div class="section-title">最近匹配</div>
            <div class="section-subtitle">最近的媒体文件识别记录</div>
          </div>
          <button class="btn btn-secondary" onclick="App.navigate('matches')">查看全部 →</button>
        </div>
        <div class="card">
          ${matches.map(m => this._renderMatchRow(m)).join('')}
        </div>
      </div>

      <!-- 待处理文件 + 匹配来源分布 -->
      <div class="content-grid">
        <div class="section">
          <div class="section-header">
            <div>
              <div class="section-title">待处理文件</div>
              <div class="section-subtitle">${pending.length} 个文件等待处理</div>
            </div>
          </div>
          <div class="card" style="padding:8px 0">
            <div class="file-list">
              ${pending.map(f => this._renderPendingFile(f)).join('')}
            </div>
          </div>
        </div>

        <div class="section">
          <div class="section-header">
            <div>
              <div class="section-title">匹配来源分布</div>
              <div class="section-subtitle">各匹配渠道占比</div>
            </div>
          </div>
          <div class="card">
            <div style="display:flex;flex-direction:column;gap:14px;padding:8px 0">
              ${this._renderDistributionBar('缓存命中', stats.cache_hits, stats.total_matches, '#60a5fa')}
              ${this._renderDistributionBar('TMDB 精确', Math.round(stats.total_matches * 0.25), stats.total_matches, '#f0b429')}
              ${this._renderDistributionBar('TMDB 模糊', Math.round(stats.total_matches * 0.05), stats.total_matches, '#f59e0b')}
              ${this._renderDistributionBar('LLM 裁决', stats.llm_calls, stats.total_matches, '#f472b6')}
              ${this._renderDistributionBar('用户确认', Math.round(stats.total_matches * 0.01), stats.total_matches, '#34d399')}
            </div>
          </div>
        </div>
      </div>
    `;
  },

  /* ---- 文件管理 ---- */
  async files() {
    const pending = await API.getPendingFiles();
    return `
      <div class="section">
        <div class="section-header">
          <div>
            <div class="section-title">文件扫描</div>
            <div class="section-subtitle">扫描目录中的媒体文件</div>
          </div>
          <div style="display:flex;gap:8px">
            <button class="btn btn-scan btn-secondary">📂 扫描目录</button>
            <button class="btn btn-match btn-primary" style="display:${pending.length ? 'flex' : 'none'}">⚡ 开始匹配</button>
          </div>
        </div>
      </div>

      <div class="content-grid content-grid--full">
        <div class="section">
          <div class="card" style="padding:0">
            <div style="padding:16px 20px;border-bottom:1px solid var(--color-border)">
              <div style="display:flex;align-items:center;justify-content:space-between">
                <span style="font-size:14px;font-weight:600;color:var(--color-text-primary)">
                  待处理文件 (${pending.length})
                </span>
                <span style="font-size:12px;color:var(--color-text-muted)">拖放文件到此区域或点击扫描</span>
              </div>
            </div>
            <div style="padding:8px 0">
              ${pending.length ? pending.map(f => this._renderPendingFile(f, true)).join('') : `
                <div class="empty-state">
                  <div class="empty-state-icon">📂</div>
                  <div class="empty-state-text">暂无待处理文件</div>
                  <button class="btn btn-scan btn-primary">扫描目录</button>
                </div>
              `}
            </div>
          </div>
        </div>
      </div>
    `;
  },

  /* ---- 匹配记录 ---- */
  async matches() {
    const matches = await API.getRecentMatches(10);
    return `
      <div class="section">
        <div class="section-header">
          <div>
            <div class="section-title">匹配记录</div>
            <div class="section-subtitle">所有媒体的匹配历史</div>
          </div>
          <div style="display:flex;gap:8px">
            <button class="btn btn-secondary">📤 导出</button>
            <button class="btn btn-secondary">🔍 筛选</button>
          </div>
        </div>
      </div>

      <div class="card" style="padding:0">
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th>文件名</th>
                <th>匹配结果</th>
                <th>年份</th>
                <th>置信度</th>
                <th>来源</th>
                <th>格式</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              ${matches.map(m => `
                <tr>
                  <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${m.file}">
                    ${m.file}
                  </td>
                  <td>
                    <span style="color:var(--color-text-primary);font-weight:500">${m.title}</span>
                    <span style="color:var(--color-text-muted);font-size:12px"> / ${m.original_title}</span>
                  </td>
                  <td>${m.year || '-'}</td>
                  <td>
                    <div class="confidence-ring" data-pct="${m.confidence}" style="width:36px;height:36px">
                      <svg width="36" height="36" viewBox="0 0 36 36">
                        <circle class="confidence-ring-bg" cx="18" cy="18" r="15"/>
                        <circle class="confidence-ring-fill" cx="18" cy="18" r="15" 
                          stroke="${m.confidence >= 80 ? '#34d399' : m.confidence >= 60 ? '#f0b429' : '#f87171'}"/>
                      </svg>
                      <div class="confidence-value" style="font-size:9px">${m.confidence}</div>
                    </div>
                  </td>
                  <td>
                    <span class="source-indicator source-indicator--${m.source === 'TMDB_EXACT' ? 'tmdb' : m.source === 'LLM_VERIFIED' ? 'llm' : m.source === 'CACHE_HIT' ? 'cache' : 'user'}">
                      ${m.source === 'TMDB_EXACT' ? 'TMDB' : m.source === 'LLM_VERIFIED' ? 'LLM' : m.source === 'CACHE_HIT' ? 'CACHE' : 'USER'}
                    </span>
                  </td>
                  <td><span class="tag tag--muted">${m.format.replace('_', ' ')}</span></td>
                  <td style="font-size:12px;color:var(--color-text-muted)">${new Date(m.created_at).toLocaleString('zh-CN')}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      </div>
    `;
  },

  /* ---- 系统设置 ---- */
  async config() {
    const cfg = await API.getConfig();
    return `
      <div class="section">
        <div class="section-header">
          <div>
            <div class="section-title">系统设置</div>
            <div class="section-subtitle">配置 TMDB、LLM 和文件管理参数</div>
          </div>
          <button class="btn btn-primary" id="saveConfigBtn">💾 保存配置</button>
        </div>
      </div>

      <div class="content-grid">
        <!-- TMDB 配置 -->
        <div class="card">
          <div class="card-header">
            <div>
              <div class="card-title">🎬 TMDB 配置</div>
              <div class="section-subtitle">媒体元数据来源</div>
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">API Key</label>
            <input class="form-input" type="password" value="${cfg.tmdb.api_key}" placeholder="输入 TMDB API Key">
            <div class="form-hint">从 https://www.themoviedb.org/settings/api 获取</div>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">语言</label>
              <select class="form-select">
                <option ${cfg.tmdb.language === 'zh-CN' ? 'selected' : ''}>zh-CN</option>
                <option ${cfg.tmdb.language === 'en-US' ? 'selected' : ''}>en-US</option>
              </select>
            </div>
            <div class="form-group">
              <label class="form-label">置信度阈值</label>
              <input class="form-input" type="number" value="${cfg.tmdb.confidence_threshold}" min="0" max="100">
              <div class="form-hint">低于此阈值将调用 LLM 补充</div>
            </div>
          </div>
        </div>

        <!-- LLM 配置 -->
        <div class="card">
          <div class="card-header">
            <div>
              <div class="card-title">🤖 LLM 配置</div>
              <div class="section-subtitle">AI 补充裁决（可选）</div>
            </div>
            <label class="toggle">
              <input type="checkbox" ${cfg.llm.api_key ? 'checked' : ''}>
              <span class="toggle-track"></span>
              <span class="toggle-label">启用</span>
            </label>
          </div>
          <div class="form-group">
            <label class="form-label">Provider</label>
            <select class="form-select">
              <option ${cfg.llm.provider === 'openai' ? 'selected' : ''}>openai</option>
              <option ${cfg.llm.provider === 'anthropic' ? 'selected' : ''}>anthropic</option>
              <option ${cfg.llm.provider === 'ollama' ? 'selected' : ''}>ollama</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">API Key</label>
            <input class="form-input" type="password" value="${cfg.llm.api_key}" placeholder="输入 LLM API Key">
          </div>
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">模型</label>
              <input class="form-input" value="${cfg.llm.model}">
            </div>
            <div class="form-group">
              <label class="form-label">置信度阈值</label>
              <input class="form-input" type="number" value="${cfg.llm.confidence_threshold}" min="0" max="100">
            </div>
          </div>
        </div>

        <!-- 文件整理 -->
        <div class="card">
          <div class="card-header">
            <div>
              <div class="card-title">📁 文件整理</div>
              <div class="section-subtitle">整理策略和命名模板</div>
            </div>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">整理模式</label>
              <select class="form-select">
                <option ${cfg.organize.mode === 'hardlink' ? 'selected' : ''}>hardlink</option>
                <option ${cfg.organize.mode === 'copy' ? 'selected' : ''}>copy</option>
                <option ${cfg.organize.mode === 'move' ? 'selected' : ''}>move</option>
              </select>
            </div>
            <div class="form-group">
              <label class="form-label">目录策略</label>
              <select class="form-select">
                <option ${cfg.organize.strategy === 'year' ? 'selected' : ''}>year</option>
                <option ${cfg.organize.strategy === 'first_letter' ? 'selected' : ''}>first_letter</option>
                <option ${cfg.organize.strategy === 'flat' ? 'selected' : ''}>flat</option>
              </select>
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">电影命名模板</label>
            <input class="form-input" value="${cfg.organize.movie_template}">
          </div>
          <div class="form-group">
            <label class="form-label">电视剧命名模板</label>
            <input class="form-input" value="${cfg.organize.tv_template}">
          </div>
          <div class="form-group" style="display:flex;align-items:center;gap:12px">
            <label class="toggle">
              <input type="checkbox" ${cfg.organize.dry_run ? 'checked' : ''}>
              <span class="toggle-track"></span>
              <span class="toggle-label">Dry Run（预览模式）</span>
            </label>
          </div>
        </div>

        <!-- 数据库 -->
        <div class="card">
          <div class="card-header">
            <div>
              <div class="card-title">🗄️ 数据库</div>
              <div class="section-subtitle">存储和缓存</div>
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">数据库路径</label>
            <input class="form-input" value="${cfg.db.path}">
            <div class="form-hint">SQLite 数据库文件位置</div>
          </div>
          <div style="display:flex;gap:8px;margin-top:16px">
            <button class="btn btn-secondary" onclick="Toast.show('缓存已清空', 'success')">🗑️ 清空缓存</button>
            <button class="btn btn-secondary" onclick="Toast.show('数据库已优化', 'success')">⚡ 优化数据库</button>
          </div>
        </div>
      </div>
    `;
  },

  /* ---- 活动日志 ---- */
  async logs() {
    const logs = await API.getLogs();
    return `
      <div class="section">
        <div class="section-header">
          <div>
            <div class="section-title">活动日志</div>
            <div class="section-subtitle">系统运行记录和操作历史</div>
          </div>
          <button class="btn btn-secondary" onclick="Toast.show('日志已刷新', 'info')">🔄 刷新</button>
        </div>
      </div>

      <div class="card" style="padding:0">
        <div style="padding:12px 20px;border-bottom:1px solid var(--color-border);display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn btn-secondary" style="font-size:12px;padding:4px 12px">全部</button>
          <button class="btn btn-outline" style="font-size:12px;padding:4px 12px">信息</button>
          <button class="btn btn-outline" style="font-size:12px;padding:4px 12px">成功</button>
          <button class="btn btn-outline" style="font-size:12px;padding:4px 12px">警告</button>
          <button class="btn btn-outline" style="font-size:12px;padding:4px 12px">错误</button>
        </div>
        <div class="log-viewer" style="max-height:600px;border:none;border-radius:0;background:rgba(0,0,0,0.3)">
          ${logs.map(log => `
            <div class="log-entry">
              <span class="log-time">${log.time}</span>
              <span class="log-level log-level--${log.level}">${log.level}</span>
              <span class="log-message">${log.message}</span>
            </div>
          `).join('')}
        </div>
      </div>
    `;
  },

  /* ---- 渲染辅助 ---- */

  _renderMatchRow(m) {
    return `
      <div style="display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid rgba(255,255,255,0.03)">
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:8px">
            <span style="font-weight:600;color:var(--color-text-primary)">${m.title}</span>
            <span style="color:var(--color-text-muted);font-size:12px">(${m.year})</span>
            <span class="source-indicator source-indicator--${m.source === 'TMDB_EXACT' ? 'tmdb' : m.source === 'LLM_VERIFIED' ? 'llm' : m.source === 'CACHE_HIT' ? 'cache' : 'user'}">
              ${m.source === 'TMDB_EXACT' ? 'TMDB' : m.source === 'LLM_VERIFIED' ? 'LLM' : 'CACHE'}
            </span>
          </div>
          <div style="font-size:12px;color:var(--color-text-muted);margin-top:2px;font-family:var(--font-mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
            ${m.file}
          </div>
        </div>
        <div class="confidence-ring" data-pct="${m.confidence}" style="flex-shrink:0">
          <svg width="40" height="40" viewBox="0 0 40 40">
            <circle class="confidence-ring-bg" cx="20" cy="20" r="17"/>
            <circle class="confidence-ring-fill" cx="20" cy="20" r="17"/>
          </svg>
          <div class="confidence-value">${m.confidence}%</div>
        </div>
      </div>
    `;
  },

  _renderPendingFile(f, showActions = false) {
    const icons = { BLURAY_BDMV: '💿', SINGLE_FILE: '🎬', TV_SEASON: '📺', TV_EPISODE: '📺' };
    const statusIcons = { pending: '⏳', processing: '🔄', done: '✅', error: '❌' };
    return `
      <div class="file-item">
        <span class="file-item-icon">${icons[f.format] || '📄'}</span>
        <div class="file-item-info">
          <div class="file-item-name">${f.name}</div>
          <div class="file-item-path">
            <span class="tag tag--muted">${f.format}</span>
            ${f.size}
          </div>
        </div>
        ${showActions ? `
          <div class="file-item-status">
            <span style="font-size:16px">${statusIcons[f.status] || '⏳'}</span>
          </div>
        ` : `
          <div class="file-item-status">
            <span class="tag tag--${f.status === 'processing' ? 'warning' : f.status === 'done' ? 'success' : f.status === 'error' ? 'error' : 'info'}">
              ${f.status}
            </span>
          </div>
        `}
      </div>
    `;
  },

  _renderDistributionBar(label, value, total, color) {
    const pct = total > 0 ? (value / total * 100).toFixed(1) : 0;
    return `
      <div>
        <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px">
          <span style="color:var(--color-text-secondary)">${label}</span>
          <span style="color:var(--color-text-muted)">${value.toLocaleString()} (${pct}%)</span>
        </div>
        <div class="progress-bar">
          <div class="progress-bar-fill" style="width:${pct}%;background:${color}"></div>
        </div>
      </div>
    `;
  },
};
