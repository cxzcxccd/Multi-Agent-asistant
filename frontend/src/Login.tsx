import { useState } from 'react';
import { Headphones, LockKeyhole, ShoppingBag } from 'lucide-react';
import { loginAccount } from './api';
import type { AuthSession } from './api';

export default function Login({
  onAuthenticated,
}: {
  onAuthenticated: (session: AuthSession) => void;
}) {
  const [username, setUsername] = useState('buyer_a');
  const [password, setPassword] = useState('buyer-a-demo');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const chooseDemoAccount = (account: 'buyer_a' | 'buyer_b' | 'staff') => {
    setUsername(account);
    if (account === 'buyer_a') setPassword('buyer-a-demo');
    if (account === 'buyer_b') setPassword('buyer-b-demo');
    if (account === 'staff') setPassword('staff-demo');
    setError('');
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      const session = await loginAccount(username, password);
      onAuthenticated(session);
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : '登录失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="login-page">
      <section className="login-card" aria-labelledby="login-title">
        <div className="login-brand">
          <span className="brand-mark">
            <ShoppingBag size={24} />
          </span>
          <div>
            <strong>极客优选</strong>
            <span>智能电商客服系统</span>
          </div>
        </div>
        <div className="login-heading">
          <span className="login-lock">
            <LockKeyhole size={20} />
          </span>
          <div>
            <h1 id="login-title">登录工作空间</h1>
            <p>系统会根据账号角色开放买家端或客服工作台。</p>
          </div>
        </div>
        <form onSubmit={submit}>
          <label>
            账号
            <input
              aria-label="账号"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
            />
          </label>
          <label>
            密码
            <input
              aria-label="密码"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
            />
          </label>
          {error && (
            <p className="login-error" role="alert">
              {error}
            </p>
          )}
          <button className="btn btn-primary login-submit" type="submit" disabled={submitting}>
            {submitting ? '正在登录…' : '登录'}
          </button>
        </form>
        <div className="demo-accounts">
          <span>演示账号</span>
          <button type="button" onClick={() => chooseDemoAccount('buyer_a')}>
            买家 A
          </button>
          <button type="button" onClick={() => chooseDemoAccount('buyer_b')}>
            买家 B
          </button>
          <button type="button" onClick={() => chooseDemoAccount('staff')}>
            <Headphones size={14} />
            客服
          </button>
        </div>
      </section>
    </main>
  );
}
