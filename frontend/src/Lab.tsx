import { useState } from 'react';
import {
  Activity,
  ArrowRight,
  BookOpen,
  ChevronRight,
  Code2,
  FlaskConical,
  Layers,
  RotateCcw,
  Settings2,
  ShieldCheck,
  Terminal,
  TriangleAlert,
} from 'lucide-react';
import { Badge, Modal, ProductCard, RunTrace } from './components';
import { DEMO_DATE, policies, products } from './data';
import { backendChatEnabled, resetDemo, setFault, useDemo } from './store';
import EvaluationPreview from './RagEvaluationPreview';
import KnowledgeLibrary from './KnowledgeLibrary';
import RunMetadata from './RunMetadata';

const evaluationPreviewEnabled = import.meta.env.VITE_RAG_EVALUATION_VISIBLE === 'true';

export default function Lab({ active = true }: { active?: boolean }) {
  const s = useDemo(),
    [tab, setTab] = useState('runs'),
    [selectedRun, setSelectedRun] = useState<string>();
  const run = s.runs.find((r) => r.id === selectedRun) || s.runs[0];
  const activeTab = !evaluationPreviewEnabled && tab === 'evaluation' ? 'runs' : tab;
  return (
    <div className="lab-page">
      <div className="page-intro">
        <div>
          <span className="eyebrow">OBSERVABILITY LAB</span>
          <h1>看得见过程，才能改进结果。</h1>
          <p>查看 LangGraph 与模拟业务的执行事件、工具调用和资料检索结果。</p>
        </div>
        <Badge tone="amber">
          <FlaskConical size={14} />
          {backendChatEnabled ? '真实后端' : '脚本演示'}
        </Badge>
      </div>
      <div className="lab-banner">
        <Code2 size={19} />
        <span>
          {backendChatEnabled
            ? '客服对话已经连接模型和 LangGraph，可查看分流、工具调用与资料检索记录。'
            : '当前使用脚本模式，执行轨迹来自演示脚本。'}
        </span>
      </div>
      <div className="tab-bar" role="tablist" aria-label="开发者功能">
        {[
          ['runs', '执行记录', Activity],
          ['knowledge', '知识库', BookOpen],
          ...(evaluationPreviewEnabled ? [['evaluation', '评测预览', FlaskConical]] : []),
          ['settings', '演示控制', Settings2],
        ].map(([id, label, Icon]) => (
          <button
            key={id as string}
            role="tab"
            aria-selected={activeTab === id}
            onClick={() => setTab(id as string)}
          >
            <Icon size={16} />
            {label as string}
          </button>
        ))}
      </div>
      {activeTab === 'runs' ? (
        <div className="run-workspace">
          <section className="run-list">
            <div className="list-heading">
              <h2>最近执行</h2>
              <span className="tiny muted">{s.runs.length} 次</span>
            </div>
            {s.runs.length ? (
              s.runs.map((r) => (
                <button
                  className={`run-item ${run?.id === r.id ? 'selected' : ''}`}
                  key={r.id}
                  onClick={() => setSelectedRun(r.id)}
                >
                  <div className="row-between">
                    <Badge
                      tone={
                        r.status === 'error' ? 'red' : r.status === 'success' ? 'green' : 'blue'
                      }
                    >
                      {r.status === 'success'
                        ? '完成'
                        : r.status === 'running'
                          ? '运行中'
                          : r.status === 'error'
                            ? '失败'
                            : '已停止'}
                    </Badge>
                    <span className="tiny muted">
                      {r.duration ? `${(r.duration / 1000).toFixed(2)}s` : '—'}
                    </span>
                  </div>
                  <strong>{r.query}</strong>
                  <span className="tiny muted">
                    {r.module} · {r.origin === 'backend' ? 'LangGraph 执行' : '脚本执行'}{' '}
                    <ChevronRight size={13} />
                  </span>
                </button>
              ))
            ) : (
              <div className="quiet-state">
                <Terminal size={27} />
                <p>等待第一次咨询</p>
                <span>到买家客服页发送问题，即可生成执行记录。</span>
              </div>
            )}
          </section>
          <section className="run-detail">
            {run ? (
              <>
                <div className="row-between">
                  <h2>执行详情</h2>
                  <Badge>{run.origin === 'backend' ? '后端执行' : '模拟运行'}</Badge>
                </div>
                <p className="run-query">{run.query}</p>
                <div className="run-metadata">
                  <div>
                    <span>任务编号</span>
                    <code>{run.id.slice(0, 8)}</code>
                  </div>
                  <div>
                    <span>模块</span>
                    <strong>{run.module}</strong>
                  </div>
                  <RunMetadata run={run} active={active && activeTab === 'runs'} />
                </div>
                <h3 className="section-title">执行时间线</h3>
                <RunTrace run={run} expanded />
                <div className="subtle-note">
                  <ShieldCheck size={16} />
                  仅记录外部动作与结果，不包含模型内部思维链。
                </div>
              </>
            ) : (
              <div className="large-empty">
                <Activity size={34} />
                <h3>每个结果，都有处理记录</h3>
                <p>这里将展示 Agent 路由、工具调用与资料检索事件。</p>
              </div>
            )}
          </section>
        </div>
      ) : activeTab === 'knowledge' ? (
        <KnowledgeLibrary active={active} />
      ) : evaluationPreviewEnabled && activeTab === 'evaluation' ? (
        <EvaluationPreview />
      ) : (
        <DemoControls active={active} />
      )}
    </div>
  );
}
function DemoControls({ active }: { active: boolean }) {
  const s = useDemo(),
    [resetting, setResetting] = useState(false);
  return (
    <section className="demo-controls">
      <div className="control-grid">
        <article className="panel">
          <div className="row-between">
            <h2>异常场景</h2>
            <TriangleAlert size={20} />
          </div>
          <p className="muted">下一次查询会使用这里的设置，切回正常模式可重试。</p>
          <label className="field">
            模拟查询模式
            <select
              aria-label="模拟查询模式"
              value={s.fault}
              onChange={(e) => setFault(e.target.value as typeof s.fault)}
            >
              <option value="none">正常运行</option>
              <option value="tool-error">工具查询失败</option>
              <option value="no-knowledge">知识资料不足</option>
            </select>
          </label>
          <p className="tiny muted">
            资料不足模式作用于商品知识查询；工具失败模式作用于实际触发查询工具的场景。
          </p>
        </article>
        <article className="panel">
          <div className="row-between">
            <h2>演示环境</h2>
            <Layers size={20} />
          </div>
          <dl className="environment-list">
            <dt>固定业务日期</dt>
            <dd>{DEMO_DATE}</dd>
            <dt>业务数据</dt>
            <dd>12 个商品 · 5 笔订单 · 2 位买家</dd>
            <dt>保存位置</dt>
            <dd>当前浏览器</dd>
            <dt>外部服务</dt>
            <dd>无</dd>
          </dl>
          <button className="btn" onClick={() => setResetting(true)}>
            <RotateCcw size={15} />
            重置演示数据
          </button>
        </article>
      </div>
      {backendChatEnabled ? (
        <KnowledgeLibrary active={active} />
      ) : (
        <article className="panel">
          <div className="row-between">
            <h2>
              <BookOpen size={19} />
              店铺规则
            </h2>
            <Badge>只读 · v1.0</Badge>
          </div>
          <div className="policy-grid">
            {policies.map((p) => (
              <div key={p.id}>
                <span className="eyebrow">{p.id}</span>
                <h3>{p.title}</h3>
                <p>{p.content}</p>
              </div>
            ))}
          </div>
        </article>
      )}
      <article className="panel">
        <div className="row-between">
          <h2>演示商品目录</h2>
          <span className="tiny muted">
            虚构品牌与规格 <ArrowRight size={13} />
          </span>
        </div>
        <div className="catalog-grid">
          {products.map((p) => (
            <ProductCard id={p.id} key={p.id} compact />
          ))}
        </div>
      </article>
      {resetting && (
        <Modal title="重置演示数据" onClose={() => setResetting(false)}>
          <p>将清除当前浏览器中的演示会话、售后申请和执行记录，恢复初始商品与订单场景。</p>
          <div className="modal-actions">
            <button className="btn" onClick={() => setResetting(false)}>
              取消
            </button>
            <button
              className="btn btn-danger"
              onClick={() => {
                resetDemo();
                setResetting(false);
              }}
            >
              确认重置
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
