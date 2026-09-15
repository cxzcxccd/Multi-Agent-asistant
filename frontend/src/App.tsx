import { useEffect, useState } from 'react';
import {
  ArrowUpRight,
  ChevronDown,
  Command,
  FlaskConical,
  Headphones,
  Menu,
  MessageSquare,
  MessageSquarePlus,
  PanelLeftClose,
  ShoppingBag,
  Sparkles,
} from 'lucide-react';
import { buyers, DEMO_DATE } from './data';
import { newConversation, selectConversation, switchBuyer, useDemo } from './store';
import { Badge } from './components';
import Chat from './Chat';
import Workbench from './Workbench';
import Lab from './Lab';
import type { BuyerId, View } from './types';

const readView = (): View => {
  const hash = location.hash.slice(1);
  return hash === 'workbench' || hash === 'lab' ? hash : 'chat';
};
const views = [
  { id: 'chat' as const, name: '买家客服', subtitle: '和小极聊聊', icon: MessageSquare },
  { id: 'workbench' as const, name: '客服工作台', subtitle: '人工服务与审核', icon: Headphones },
  { id: 'lab' as const, name: '开发者与评测', subtitle: '执行记录与实验', icon: FlaskConical },
];
export default function App() {
  const s = useDemo(),
    [view, setView] = useState<View>(readView),
    [navOpen, setNavOpen] = useState(false);
  useEffect(() => {
    const handle = () => setView(readView());
    window.addEventListener('hashchange', handle);
    return () => window.removeEventListener('hashchange', handle);
  }, []);
  const navigate = (next: View) => {
    location.hash = next;
    setView(next);
    setNavOpen(false);
  };
  const current = views.find((v) => v.id === view)!;
  const pending =
    s.requests.filter((r) => r.status === 'pending').length +
    s.conversations.filter((c) => c.mode === 'waiting').length;
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        跳转到主要内容
      </a>
      {navOpen && (
        <button
          className="nav-backdrop"
          aria-label="关闭导航遮罩"
          onClick={() => setNavOpen(false)}
        />
      )}
      <aside className={`sidebar ${navOpen ? 'is-open' : ''}`}>
        <a className="brand" href="#chat" onClick={() => setNavOpen(false)}>
          <span className="brand-mark">
            <Command size={25} />
          </span>
          <span>
            <strong>极客优选</strong>
            <small>GEEK SELECT</small>
          </span>
        </a>
        <button
          className="icon-button close-nav"
          aria-label="收起导航"
          onClick={() => setNavOpen(false)}
        >
          <PanelLeftClose size={20} />
        </button>
        <div className="store-switch">
          <span className="store-icon">
            <ShoppingBag size={18} />
          </span>
          <div>
            <strong>数码旗舰店</strong>
            <span>独立网页体验</span>
          </div>
          <Badge>DEMO</Badge>
        </div>
        <span className="nav-label">工作空间</span>
        <nav aria-label="主导航">
          {views.map((v) => (
            <a
              href={`#${v.id}`}
              className={`nav-item ${view === v.id ? 'active' : ''}`}
              key={v.id}
              onClick={() => navigate(v.id)}
              aria-current={view === v.id ? 'page' : undefined}
            >
              <v.icon size={19} />
              <span>{v.name}</span>
              {v.id === 'workbench' && pending > 0 ? (
                <span className="nav-count">{pending}</span>
              ) : null}
            </a>
          ))}
        </nav>
        <div className="history-heading">
          <span className="nav-label">最近咨询</span>
          <button
            className="icon-button"
            aria-label="新建会话"
            onClick={() => {
              newConversation();
              navigate('chat');
            }}
          >
            <MessageSquarePlus size={16} />
          </button>
        </div>
        <div className="history-list">
          {s.conversations
            .filter((c) => c.buyer === s.buyer)
            .slice(0, 6)
            .map((c) => (
              <button
                className={s.active[s.buyer] === c.id && view === 'chat' ? 'selected' : ''}
                key={c.id}
                onClick={() => {
                  selectConversation(c.id);
                  navigate('chat');
                }}
              >
                <MessageSquare size={14} />
                <span>{c.title}</span>
              </button>
            ))}
        </div>
        <div className="sidebar-bottom">
          <div className="demo-note">
            <span className="demo-label">
              <span />
              交互演示环境
            </span>
            <p>模拟业务数据，体验完整服务流程。</p>
            <span className="tiny">固定业务日期 {DEMO_DATE}</span>
          </div>
          <button className="sidebar-profile" onClick={() => navigate('lab')}>
            <span className="profile-mark">
              <Sparkles size={18} />
            </span>
            <div>
              <strong>Agent Playground</strong>
              <span>Frontend Demo · v0.1</span>
            </div>
            <ArrowUpRight size={16} />
          </button>
        </div>
      </aside>
      <div className="main-shell">
        <header className="app-header">
          <button
            className="icon-button menu-button"
            aria-label="打开导航"
            onClick={() => setNavOpen(true)}
          >
            <Menu size={23} />
          </button>
          <div className="breadcrumb">
            <span>工作空间</span>
            <span>/</span>
            <strong>{current.name}</strong>
          </div>
          <div className="header-right">
            <Badge tone="amber">
              <span className="status-dot" />
              模拟数据
            </Badge>
            <span className="header-divider" />
            {view === 'chat' ? (
              <label className="buyer-switch">
                <span className="header-avatar">{buyers[s.buyer][0]}</span>
                <select
                  aria-label="切换模拟买家"
                  value={s.buyer}
                  onChange={(e) => switchBuyer(e.target.value as BuyerId)}
                >
                  <option value="A">林同学 · 买家 A</option>
                  <option value="B">陈同学 · 买家 B</option>
                </select>
                <ChevronDown size={13} />
              </label>
            ) : (
              <span className="header-role">
                {view === 'workbench' ? '客服小周 · 模拟客服' : '开发者视图'}
              </span>
            )}
          </div>
        </header>
        {s.storageWarning && (
          <div className="storage-warning" role="alert">
            {s.storageWarning}
          </div>
        )}
        <main id="main-content" className={`main-content view-${view}`}>
          {view === 'chat' ? <Chat /> : view === 'workbench' ? <Workbench /> : <Lab />}
        </main>
        <footer className="app-footer">
          <span>
            <span className="status-dot online" />
            本地演示运行中
          </span>
          <span>业务数据模拟 · AI 行为脚本驱动</span>
        </footer>
      </div>
    </div>
  );
}
