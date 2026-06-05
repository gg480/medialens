/**
 * MediaLens — Main Application
 * SPA 控制器：路由、导航、页面管理
 */

const App = {
  currentView: 'dashboard',

  /* ---- 初始化 ---- */
  async init() {
    this._cacheDOM();
    this._bindEvents();
    this._setupAmbientOrbs();
    await this.navigate('dashboard');
  },

  _cacheDOM() {
    this.navItems = document.querySelectorAll('.nav-item');
    this.pages = document.querySelectorAll('.page-view');
    this.headerTitle = document.getElementById('headerTitle');
    this.contentArea = document.getElementById('pageContent');
    this.mobileMenuBtn = document.getElementById('mobileMenuBtn');
    this.sidebar = document.getElementById('sidebar');
  },

  _bindEvents() {
    // 导航点击
    this.navItems.forEach(item => {
      item.addEventListener('click', (e) => {
        e.preventDefault();
        const view = item.dataset.view;
        if (view) this.navigate(view);
        // 移动端关闭侧栏
        if (window.innerWidth <= 768) {
          this.sidebar.classList.remove('open');
        }
      });
    });

    // 移动端菜单
    this.mobileMenuBtn?.addEventListener('click', () => {
      this.sidebar.classList.toggle('open');
    });
  },

  _setupAmbientOrbs() {
    // Ambient orbs are in HTML, no JS setup needed
  },

  /* ---- 路由 ---- */
  async navigate(view) {
    this.currentView = view;

    // 更新导航高亮
    this.navItems.forEach(item => {
      item.classList.toggle('active', item.dataset.view === view);
    });

    // 更新标题
    const labels = {
      dashboard: '仪表盘',
      files: '文件管理',
      matches: '匹配记录',
      config: '系统设置',
      logs: '活动日志',
    };
    this.headerTitle.textContent = labels[view] || view;

    // 加载页面内容
    await this._loadView(view);
  },

  async _loadView(view) {
    this.contentArea.innerHTML = AppViews.getSkeleton(view);

    // 异步加载（模拟网络延迟）
    await new Promise(r => setTimeout(r, 200));

    try {
      let html = '';
      switch (view) {
        case 'dashboard':
          html = await AppViews.dashboard();
          break;
        case 'files':
          html = await AppViews.files();
          break;
        case 'matches':
          html = await AppViews.matches();
          break;
        case 'config':
          html = await AppViews.config();
          break;
        case 'logs':
          html = await AppViews.logs();
          break;
      }
      this.contentArea.innerHTML = html;
      this.contentArea.classList.add('page-view');

      // 页面特定初始化
      if (view === 'dashboard') this._initDashboardCharts();
      if (view === 'config') this._initConfigForm();
      if (view === 'files') this._initFileActions();
      if (view === 'matches') this._initMatchActions();
    } catch (err) {
      this.contentArea.innerHTML = `
        <div class="empty-state">
          <div class="empty-state-icon">⚠️</div>
          <div class="empty-state-text">加载失败: ${err.message}</div>
          <button class="btn btn-primary" onclick="App.navigate('${view}')">重试</button>
        </div>
      `;
    }
  },

  _initDashboardCharts() {
    // 绘制置信度环形图
    document.querySelectorAll('.confidence-ring').forEach(el => {
      const pct = parseFloat(el.dataset.pct || 0);
      const circle = el.querySelector('.confidence-ring-fill');
      const val = el.querySelector('.confidence-value');
      if (circle) {
        const r = circle.getAttribute('r');
        const circ = 2 * Math.PI * r;
        circle.style.strokeDasharray = circ;
        circle.style.strokeDashoffset = circ - (pct / 100) * circ;
      }
      if (val) val.textContent = `${Math.round(pct)}%`;
    });
  },

  _initConfigForm() {
    document.querySelectorAll('.toggle input').forEach(toggle => {
      toggle.addEventListener('change', () => {
        Toast.show('设置已更新', 'success');
      });
    });

    document.getElementById('saveConfigBtn')?.addEventListener('click', async () => {
      const btn = document.getElementById('saveConfigBtn');
      btn.textContent = '保存中…';
      btn.disabled = true;
      await new Promise(r => setTimeout(r, 800));
      btn.textContent = '保存配置';
      btn.disabled = false;
      Toast.show('配置已保存', 'success');
    });
  },

  _initFileActions() {
    document.querySelectorAll('.btn-scan').forEach(btn => {
      btn.addEventListener('click', async () => {
        btn.textContent = '扫描中…';
        btn.disabled = true;
        await new Promise(r => setTimeout(r, 1500));
        btn.textContent = '扫描目录';
        btn.disabled = false;
        Toast.show('扫描完成，发现 4 个新文件', 'success');
        await App.navigate('files');
      });
    });
  },

  _initMatchActions() {
    document.querySelectorAll('.btn-match').forEach(btn => {
      btn.addEventListener('click', async () => {
        btn.textContent = '匹配中…';
        btn.disabled = true;
        await new Promise(r => setTimeout(r, 2000));
        btn.textContent = '开始匹配';
        btn.disabled = false;
        Toast.show('匹配完成，3 个文件已识别', 'success');
        await App.navigate('matches');
      });
    });
  },
};

/* ---- Toast 通知 ---- */
const Toast = {
  show(message, type = 'info', duration = 3000) {
    const container = document.getElementById('toastContainer');
    const icons = {
      success: '✓',
      error: '✗',
      warning: '⚠',
      info: 'ℹ',
    };
    const toast = document.createElement('div');
    toast.className = `toast toast--${type}`;
    toast.setAttribute('role', 'alert');
    toast.innerHTML = `
      <span aria-hidden="true">${icons[type] || 'ℹ'}</span>
      <span>${message}</span>
    `;
    container.appendChild(toast);

    setTimeout(() => {
      toast.classList.add('toast-exit');
      setTimeout(() => toast.remove(), 300);
    }, duration);
  },
};

/* ---- 启动 ---- */
document.addEventListener('DOMContentLoaded', () => App.init());
