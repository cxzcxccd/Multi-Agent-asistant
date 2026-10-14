import { useEffect, useState } from 'react';
import { getRuntimeStatus } from './api';
import { backendChatEnabled } from './store';
import type { Run } from './types';

const serverLabel = (server: string) =>
  server === 'catalog' ? '商品' : server === 'orders' ? '订单' : server;

export default function RunMetadata({ run, active }: { run: Run; active: boolean }) {
  const [config, setConfig] = useState<Awaited<ReturnType<typeof getRuntimeStatus>>>();
  useEffect(() => {
    if (!backendChatEnabled || !active) return;
    const controller = new AbortController();
    void getRuntimeStatus(controller.signal)
      .then(setConfig)
      .catch(() => {});
    return () => controller.abort();
  }, [active]);
  const real = run.origin === 'backend';
  const telemetry = run.telemetry;
  const model =
    telemetry?.model_names?.join('、') ||
    telemetry?.model_name ||
    (config?.model_configured ? config.model_name : null);
  const usage = telemetry?.token_usage;
  const calls = telemetry?.mcp_calls || [];
  const enabled = telemetry?.mcp_enabled || config?.mcp_enabled;
  return (
    <>
      <div className="run-model-metadata">
        <span>模型 / Token</span>
        <strong>
          {!real
            ? '脚本演示 / 不调用模型'
            : `${model || (run.status === 'running' ? '等待模型信息' : '模型名称未记录')} / ${usage ? `${usage.total_tokens.toLocaleString()} Token` : telemetry ? '服务未返回用量' : '历史记录未采集用量'}`}
        </strong>
        {real && usage && (
          <small>
            输入 {usage.input_tokens.toLocaleString()} · 输出 {usage.output_tokens.toLocaleString()}
            {telemetry!.usage_calls < telemetry!.model_calls
              ? ` · 部分用量（${telemetry!.usage_calls}/${telemetry!.model_calls} 次调用）`
              : ''}
          </small>
        )}
        {real && !telemetry && config?.model_configured && (
          <small>模型名称来自当前配置；旧记录的 Token 无法补算。</small>
        )}
      </div>
      <div className="run-mcp-metadata">
        <span>MCP</span>
        <strong>
          {!real
            ? '脚本演示 / 不调用 MCP'
            : calls.length
              ? `${calls.reduce((sum, item) => sum + item.calls, 0)} 次调用 · ${calls.reduce((sum, item) => sum + item.successes, 0)} 次成功`
              : enabled?.length
                ? `已启用${enabled.map(serverLabel).join('、')} MCP${telemetry ? ' · 本次未调用' : '（当前配置）'}`
                : telemetry || enabled
                  ? '本次使用本地工具，MCP 未启用'
                  : '此记录未采集 MCP 状态'}
        </strong>
        {real && calls.length > 0 && (
          <small>
            {calls
              .map(
                (item) =>
                  `${serverLabel(item.server)} / ${item.tool}：${item.successes}/${item.calls} 成功`,
              )
              .join('；')}
          </small>
        )}
        {real && !telemetry && enabled?.length ? (
          <small>旧记录未采集调用次数；已启用不代表连通性检测。</small>
        ) : null}
      </div>
    </>
  );
}
