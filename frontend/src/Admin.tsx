import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Boxes, Package, Pencil, Plus, RefreshCw, Save, Trash2 } from 'lucide-react';
import {
  adjustStock,
  createProduct,
  deleteProduct,
  getAllProducts,
  updateProduct,
  type ProductCreatePayload,
  type StockOperation,
} from './api';
import { Badge, Modal } from './components';
import type { Category, Product } from './types';
import './admin.css';

type DialogState =
  | { type: 'closed' }
  | { type: 'create' }
  | { type: 'edit'; product: Product }
  | { type: 'stock'; product: Product }
  | { type: 'delete'; product: Product };

interface ProductForm {
  name: string;
  category: Category;
  series: string;
  price: string;
  stock: string;
  specs: string;
  description: string;
  color: string;
  source: string;
}

const emptyForm: ProductForm = {
  name: '',
  category: '耳机',
  series: '',
  price: '',
  stock: '',
  specs: '',
  description: '',
  color: '',
  source: '手动录入',
};

const toForm = (product: Product): ProductForm => ({
  name: product.name,
  category: product.category,
  series: product.series,
  price: product.price.toString(),
  stock: product.stock.toString(),
  specs: product.specs.join('\n'),
  description: product.description,
  color: product.color,
  source: product.source,
});

const parseSpecs = (value: string): string[] =>
  value
    .split('\n')
    .map((item) => item.trim())
    .filter((item) => item.length > 0);

export default function Admin() {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [dialog, setDialog] = useState<DialogState>({ type: 'closed' });
  const [form, setForm] = useState<ProductForm>(emptyForm);
  const [formError, setFormError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState('');
  const [stockOperation, setStockOperation] = useState<StockOperation>('add');
  const [stockQuantity, setStockQuantity] = useState('');

  const loadProducts = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const allProducts = await getAllProducts();
      setProducts(allProducts);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '商品加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadProducts();
  }, [loadProducts]);

  const flash = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(''), 2600);
  };

  const openCreate = () => {
    setForm(emptyForm);
    setFormError('');
    setDialog({ type: 'create' });
  };
  const openEdit = (product: Product) => {
    setForm(toForm(product));
    setFormError('');
    setDialog({ type: 'edit', product });
  };
  const openStock = (product: Product) => {
    setStockOperation('add');
    setStockQuantity('');
    setFormError('');
    setDialog({ type: 'stock', product });
  };

  const upsertProduct = (updated: Product) => {
    setProducts((current) => {
      const exists = current.some((item) => item.id === updated.id);
      return exists
        ? current.map((item) => (item.id === updated.id ? updated : item))
        : [...current, updated].sort((a, b) => a.id.localeCompare(b.id));
    });
  };

  const submitForm = async () => {
    const price = Number.parseInt(form.price, 10);
    const stock = Number.parseInt(form.stock, 10);
    const specs = parseSpecs(form.specs);
    if (
      !form.name.trim() ||
      !form.series.trim() ||
      !form.description.trim() ||
      !form.color.trim()
    ) {
      setFormError('名称、系列、颜色和描述不能为空。');
      return;
    }
    if (!Number.isFinite(price) || price < 0 || !Number.isFinite(stock) || stock < 0) {
      setFormError('价格和库存必须是非负整数。');
      return;
    }
    if (specs.length === 0) {
      setFormError('至少填写一条规格（每行一条）。');
      return;
    }

    const commonFields = {
      name: form.name.trim(),
      category: form.category,
      series: form.series.trim(),
      price,
      specs,
      description: form.description.trim(),
      color: form.color.trim(),
      source: form.source.trim() || '手动录入',
    };

    setSubmitting(true);
    setFormError('');
    try {
      let saved: Product | null = null;
      if (dialog.type === 'create') {
        const createPayload: ProductCreatePayload = {
          ...commonFields,
          stock,
        };
        saved = await createProduct(createPayload);
      }
      if (dialog.type === 'edit') {
        saved = await updateProduct(dialog.product.id, commonFields);
      }
      if (saved === null) throw new Error('表单状态异常');
      upsertProduct(saved);
      flash(dialog.type === 'create' ? `已新增商品 ${saved.id}` : `已保存 ${saved.id}`);
      setDialog({ type: 'closed' });
    } catch (caught) {
      setFormError(caught instanceof Error ? caught.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  const submitStock = async () => {
    const quantity = Number.parseInt(stockQuantity, 10);
    if (dialog.type !== 'stock') return;
    if (!Number.isFinite(quantity) || quantity < 0) {
      setFormError('数量必须是非负整数。');
      return;
    }
    if (stockOperation === 'subtract' && quantity > dialog.product.stock) {
      setFormError(`库存不足：当前 ${dialog.product.stock} 件，无法扣减 ${quantity} 件。`);
      return;
    }

    setSubmitting(true);
    setFormError('');
    try {
      const updated = await adjustStock(dialog.product.id, stockOperation, quantity);
      upsertProduct(updated);
      const labels: Record<StockOperation, string> = {
        set: '盘点',
        add: '入库',
        subtract: '出库',
      };
      flash(`${labels[stockOperation]}完成，${updated.id} 当前库存 ${updated.stock} 件`);
      setDialog({ type: 'closed' });
    } catch (caught) {
      setFormError(caught instanceof Error ? caught.message : '库存调整失败');
    } finally {
      setSubmitting(false);
    }
  };

  const confirmDelete = async () => {
    if (dialog.type !== 'delete') return;
    setSubmitting(true);
    setFormError('');
    try {
      await deleteProduct(dialog.product.id);
      setProducts((current) => current.filter((item) => item.id !== dialog.product.id));
      flash(`已删除商品 ${dialog.product.id}`);
      setDialog({ type: 'closed' });
    } catch (caught) {
      setFormError(caught instanceof Error ? caught.message : '删除失败');
    } finally {
      setSubmitting(false);
    }
  };

  const totalStock = products.reduce((sum, product) => sum + product.stock, 0);
  const outOfStock = products.filter((product) => product.stock === 0).length;

  const set = (patch: Partial<ProductForm>) => setForm((current) => ({ ...current, ...patch }));

  const stockPreview = (() => {
    if (dialog.type !== 'stock') return null;
    const quantity = Number.parseInt(stockQuantity, 10);
    if (!Number.isFinite(quantity) || quantity < 0) return null;
    const current = dialog.product.stock;
    const next =
      stockOperation === 'set'
        ? quantity
        : stockOperation === 'add'
          ? current + quantity
          : current - quantity;
    return next < 0 ? '将出现负库存' : `调整后库存：${next} 件`;
  })();

  return (
    <div className="admin-page">
      <section className="admin-hero">
        <div className="admin-hero-copy">
          <span className="admin-hero-icon">
            <Boxes size={26} />
          </span>
          <div>
            <h1>商品与库存管理</h1>
            <p>维护数码旗舰店的商品信息、价格与库存数量</p>
          </div>
        </div>
        <div className="admin-hero-actions">
          <button className="btn" onClick={() => void loadProducts()} aria-label="刷新商品列表">
            <RefreshCw size={15} /> 刷新
          </button>
          <button className="btn btn-primary" onClick={openCreate} data-testid="add-product">
            <Plus size={16} /> 新增商品
          </button>
        </div>
      </section>

      <section className="admin-stat-row">
        <div className="admin-stat">
          <Package size={18} />
          <div>
            <strong>{products.length}</strong>
            <span>在售商品种类</span>
          </div>
        </div>
        <div className="admin-stat">
          <Boxes size={18} />
          <div>
            <strong>{totalStock}</strong>
            <span>库存总件数</span>
          </div>
        </div>
        <div className="admin-stat">
          <AlertTriangle size={18} />
          <div>
            <strong>{outOfStock}</strong>
            <span>缺货种类</span>
          </div>
        </div>
      </section>

      {notice && (
        <div className="admin-notice" role="status" data-testid="admin-notice">
          {notice}
        </div>
      )}

      <section className="admin-table-card">
        {loading ? (
          <div className="admin-placeholder">正在加载商品…</div>
        ) : error ? (
          <div className="admin-placeholder admin-error-state" role="alert">
            <p>{error}</p>
            <button className="btn" onClick={() => void loadProducts()}>
              重新加载
            </button>
          </div>
        ) : (
          <div className="admin-table-scroll">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>编号</th>
                  <th>商品名称</th>
                  <th>分类</th>
                  <th>系列</th>
                  <th className="num">价格</th>
                  <th className="num">库存</th>
                  <th>状态</th>
                  <th className="admin-actions-col">操作</th>
                </tr>
              </thead>
              <tbody>
                {products.map((product) => (
                  <tr key={product.id} data-testid={`admin-row-${product.id}`}>
                    <td className="mono">{product.id.toUpperCase()}</td>
                    <td className="product-name-cell">{product.name}</td>
                    <td>{product.category}</td>
                    <td>{product.series}</td>
                    <td className="num">¥{product.price}</td>
                    <td className="num strong">{product.stock}</td>
                    <td>
                      {product.stock > 0 ? (
                        <Badge tone="green">有货</Badge>
                      ) : (
                        <Badge tone="red">缺货</Badge>
                      )}
                    </td>
                    <td>
                      <div className="admin-row-actions">
                        <button
                          className="admin-icon-action"
                          aria-label={`调整 ${product.name} 库存`}
                          title="库存调整"
                          onClick={() => openStock(product)}
                        >
                          <Boxes size={16} />
                        </button>
                        <button
                          className="admin-icon-action"
                          aria-label={`编辑 ${product.name}`}
                          title="编辑商品"
                          onClick={() => openEdit(product)}
                        >
                          <Pencil size={16} />
                        </button>
                        <button
                          className="admin-icon-action danger"
                          aria-label={`删除 ${product.name}`}
                          title="删除商品"
                          onClick={() => setDialog({ type: 'delete', product })}
                        >
                          <Trash2 size={16} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <p className="admin-foot-note">
        商品与库存数据通过后端接口实时读写；删除商品不可恢复，请谨慎操作。
      </p>

      {(dialog.type === 'create' || dialog.type === 'edit') && (
        <Modal
          title={dialog.type === 'create' ? '新增商品' : `编辑商品 ${dialog.product.id}`}
          onClose={() => setDialog({ type: 'closed' })}
        >
          <div className="admin-form">
            <label className="admin-field">
              商品名称
              <input
                value={form.name}
                maxLength={200}
                onChange={(e) => set({ name: e.target.value })}
                placeholder="例如 AirBeat Pro 降噪耳机"
              />
            </label>
            <div className="admin-form-grid">
              <label className="admin-field">
                分类
                <select
                  value={form.category}
                  onChange={(e) => set({ category: e.target.value as Category })}
                >
                  <option value="耳机">耳机</option>
                  <option value="充电器">充电器</option>
                  <option value="扩展坞">扩展坞</option>
                </select>
              </label>
              <label className="admin-field">
                系列
                <input
                  value={form.series}
                  maxLength={100}
                  onChange={(e) => set({ series: e.target.value })}
                />
              </label>
              <label className="admin-field">
                价格（元）
                <input
                  type="number"
                  min={0}
                  value={form.price}
                  onChange={(e) => set({ price: e.target.value })}
                />
              </label>
              <label className="admin-field">
                库存（件）
                <input
                  type="number"
                  min={0}
                  value={form.stock}
                  disabled={dialog.type === 'edit'}
                  onChange={(e) => set({ stock: e.target.value })}
                />
                {dialog.type === 'edit' && <small>库存请使用列表中的“库存调整”。</small>}
              </label>
            </div>
            <label className="admin-field">
              规格（每行一条）
              <textarea
                rows={3}
                value={form.specs}
                onChange={(e) => set({ specs: e.target.value })}
                placeholder={'主动降噪\n蓝牙 5.3'}
              />
            </label>
            <label className="admin-field">
              商品描述
              <textarea
                rows={2}
                maxLength={1000}
                value={form.description}
                onChange={(e) => set({ description: e.target.value })}
              />
            </label>
            <div className="admin-form-grid">
              <label className="admin-field">
                颜色标识
                <input
                  value={form.color}
                  maxLength={50}
                  onChange={(e) => set({ color: e.target.value })}
                />
              </label>
              <label className="admin-field">
                来源
                <input
                  value={form.source}
                  maxLength={300}
                  onChange={(e) => set({ source: e.target.value })}
                />
              </label>
            </div>

            {formError && (
              <p className="admin-form-error" role="alert">
                {formError}
              </p>
            )}
            <div className="modal-actions">
              <button
                className="btn"
                onClick={() => setDialog({ type: 'closed' })}
                disabled={submitting}
              >
                取消
              </button>
              <button
                className="btn btn-primary"
                onClick={() => void submitForm()}
                disabled={submitting}
                data-testid="save-product"
              >
                <Save size={15} /> {submitting ? '保存中…' : '保存商品'}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {dialog.type === 'stock' && (
        <Modal
          title={`库存调整 · ${dialog.product.id}`}
          onClose={() => setDialog({ type: 'closed' })}
        >
          <div className="admin-stock-dialog">
            <p className="admin-stock-current">
              {dialog.product.name}　当前库存 <strong>{dialog.product.stock}</strong> 件
            </p>
            <div className="admin-stock-modes" role="radiogroup" aria-label="库存调整方式">
              {(['add', 'subtract', 'set'] as StockOperation[]).map((operation) => {
                const labels: Record<StockOperation, string> = {
                  add: '入库 +',
                  subtract: '出库 −',
                  set: '盘点 =',
                };
                return (
                  <button
                    key={operation}
                    className={stockOperation === operation ? 'selected' : ''}
                    aria-pressed={stockOperation === operation}
                    onClick={() => setStockOperation(operation)}
                  >
                    {labels[operation]}
                  </button>
                );
              })}
            </div>
            <label className="admin-field">
              数量
              <input
                type="number"
                min={0}
                value={stockQuantity}
                autoFocus
                data-testid="stock-quantity"
                onChange={(e) => setStockQuantity(e.target.value)}
              />
            </label>
            <p
              className={`admin-stock-preview ${stockPreview === '将出现负库存' ? 'invalid' : ''}`}
            >
              {stockPreview || '输入数量后预览结果'}
            </p>
            {formError && (
              <p className="admin-form-error" role="alert">
                {formError}
              </p>
            )}
            <div className="modal-actions">
              <button
                className="btn"
                onClick={() => setDialog({ type: 'closed' })}
                disabled={submitting}
              >
                取消
              </button>
              <button
                className="btn btn-primary"
                onClick={() => void submitStock()}
                disabled={submitting}
                data-testid="confirm-stock"
              >
                {submitting ? '处理中…' : '确认调整'}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {dialog.type === 'delete' && (
        <Modal title="删除商品" onClose={() => setDialog({ type: 'closed' })}>
          <div className="admin-delete-dialog">
            <p>
              确定删除商品 <strong>{dialog.product.name}</strong>（编号 {dialog.product.id}）吗？
            </p>
            <p className="admin-delete-warning">
              <AlertTriangle size={16} /> 删除后商品将从旗舰店移除，此操作不可恢复。
            </p>
            {formError && (
              <p className="admin-form-error" role="alert">
                {formError}
              </p>
            )}
            <div className="modal-actions">
              <button
                className="btn"
                onClick={() => setDialog({ type: 'closed' })}
                disabled={submitting}
              >
                取消
              </button>
              <button
                className="btn btn-danger"
                onClick={() => void confirmDelete()}
                disabled={submitting}
                data-testid="confirm-delete"
              >
                <Trash2 size={15} /> {submitting ? '删除中…' : '确认删除'}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
