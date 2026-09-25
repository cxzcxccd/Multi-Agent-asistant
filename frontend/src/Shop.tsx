import { useEffect, useState } from 'react';
import {
  ArrowDownUp,
  ArrowLeft,
  ArrowRight,
  Check,
  Headphones,
  LayoutGrid,
  MessageSquare,
  PackageSearch,
  Search,
  ShieldCheck,
  ShoppingBag,
  SlidersHorizontal,
  X,
} from 'lucide-react';
import { getProductDetail, getProducts } from './api';
import { money, policies } from './data';
import { Badge, Modal, SourceList } from './components';
import type { Category, Product, View } from './types';

type CategoryFilter = '全部商品' | Category;
type PriceFilter = 'all' | 'under200' | '200to400' | 'over400';
type ProductSort = 'default' | 'price-asc' | 'price-desc';

const categories: CategoryFilter[] = ['全部商品', '耳机', '充电器', '扩展坞'];

export function CatalogPhoto({ product }: { product: Product }) {
  const productNumber = Number.parseInt(product.id.slice(1), 10);
  const index = Number.isNaN(productNumber) ? 0 : Math.max(productNumber - 1, 0);
  return (
    <div
      className="catalog-photo"
      role="img"
      aria-label={`${product.name}外观示意图`}
      style={{ backgroundPosition: `${((index % 4) * 100) / 3}% ${Math.floor(index / 4) * 50}%` }}
    />
  );
}
export default function Shop({
  origin,
  onBack,
  onProductAction,
}: {
  origin: Exclude<View, 'shop'>;
  onBack: () => void;
  onProductAction: (id: string) => void;
}) {
  const [category, setCategory] = useState<CategoryFilter>('全部商品');
  const [query, setQuery] = useState('');
  const [queryInput, setQueryInput] = useState('');
  const [sort, setSort] = useState<ProductSort>('default');
  const [onlyStock, setOnlyStock] = useState(false);
  const [budget, setBudget] = useState<PriceFilter>('all');
  const [selected, setSelected] = useState<string>();
  const [products, setProducts] = useState<Product[]>([]);
  const [total, setTotal] = useState(0);
  const [catalogTotal, setCatalogTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [catalogError, setCatalogError] = useState('');
  const [reloadVersion, setReloadVersion] = useState(0);
  const [selectedProduct, setSelectedProduct] = useState<Product>();
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const staff = origin === 'workbench';

  useEffect(() => {
    const controller = new AbortController();

    async function loadProducts() {
      setLoading(true);
      setCatalogError('');

      try {
        let minPrice: number | undefined;
        let maxPrice: number | undefined;

        if (budget === 'under200') {
          maxPrice = 200;
        } else if (budget === '200to400') {
          minPrice = 201;
          maxPrice = 400;
        } else if (budget === 'over400') {
          minPrice = 401;
        }

        const response = await getProducts(
          {
            keyword: query || undefined,
            category: category === '全部商品' ? undefined : category,
            minPrice,
            maxPrice,
            inStock: onlyStock,
            sort,
            limit: 50,
          },
          controller.signal,
        );

        setProducts(response.items);
        setTotal(response.total);
        if (!query && category === '全部商品' && budget === 'all' && !onlyStock) {
          setCatalogTotal(response.total);
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return;
        }
        setProducts([]);
        setTotal(0);
        setCatalogError(error instanceof Error ? error.message : '商品列表加载失败');
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    }

    void loadProducts();
    return () => controller.abort();
  }, [budget, category, onlyStock, query, reloadVersion, sort]);

  useEffect(() => {
    if (!selected) {
      setSelectedProduct(undefined);
      setDetailError('');
      return;
    }

    const controller = new AbortController();
    const productId = selected;

    async function loadProductDetail() {
      setSelectedProduct(undefined);
      setDetailLoading(true);
      setDetailError('');

      try {
        const product = await getProductDetail(productId, controller.signal);
        setSelectedProduct(product);
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return;
        }
        setDetailError(error instanceof Error ? error.message : '商品详情加载失败');
      } finally {
        if (!controller.signal.aborted) {
          setDetailLoading(false);
        }
      }
    }

    void loadProductDetail();
    return () => controller.abort();
  }, [selected]);
  const clear = () => {
    setCategory('全部商品');
    setQuery('');
    setQueryInput('');
    setBudget('all');
    setOnlyStock(false);
    setSort('default');
  };
  const act = (id: string) => {
    setSelected(undefined);
    onProductAction(id);
  };
  return (
    <div className="shop-page">
      <div className="shop-utility">
        <button className="text-button" onClick={onBack}>
          <ArrowLeft size={15} />
          {staff ? '返回客服工作台' : origin === 'lab' ? '返回开发者页' : '返回当前咨询'}
        </button>
        <span>
          <ShieldCheck size={14} />
          浏览商品不会清空会话或工单
        </span>
      </div>
      <section className="shop-header">
        <div className="shop-identity">
          <span className="shop-logo">
            <ShoppingBag size={32} />
          </span>
          <div>
            <div className="shop-name">
              <h1>极客优选数码旗舰店</h1>
              <Badge tone="blue">演示店铺</Badge>
            </div>
            <p>
              耳机 · 充电器 · 扩展坞 <span> / </span> 为你的日常，选一件好装备
            </p>
          </div>
        </div>
        <form
          className="shop-search"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            setQuery(queryInput.trim());
          }}
        >
          <Search size={19} />
          <input
            aria-label="搜索店内商品"
            placeholder="搜索商品名称、型号或参数"
            value={queryInput}
            onChange={(e) => setQueryInput(e.target.value)}
          />
          {queryInput && (
            <button
              type="button"
              aria-label="清空商品搜索"
              onClick={() => {
                setQuery('');
                setQueryInput('');
              }}
            >
              <X size={16} />
            </button>
          )}
          <button className="search-submit" type="submit">
            搜本店
          </button>
        </form>
      </section>
      <nav className="shop-category-nav" aria-label="商品分类">
        {categories.map((cat) => (
          <button key={cat} aria-pressed={category === cat} onClick={() => setCategory(cat)}>
            {cat === '全部商品' && <LayoutGrid size={17} />}
            {cat}
            {cat === '全部商品' && catalogTotal > 0 && <span>{catalogTotal}</span>}
          </button>
        ))}
        <div>
          <Headphones size={16} />
          咨询与售后，全程有人接手
        </div>
      </nav>
      <div className="shop-collection">
        <div className="collection-mark">
          <span>GEEK SELECT / COLLECTION</span>
          <strong>好声音，好连接，好状态。</strong>
        </div>
        <div className="collection-stats">
          <span>
            <strong>{catalogTotal || '—'}</strong> 件数码好物
          </span>
          <span>
            <strong>3</strong> 大商品分类
          </span>
          <span>
            <Check size={17} />
            参数与库存可查
          </span>
        </div>
      </div>
      <section className="shop-catalog">
        <div className="catalog-heading">
          <div>
            <h2>{query ? `“${query}”的搜索结果` : category}</h2>
            <span aria-live="polite">共 {total} 件商品</span>
          </div>
          <span className="catalog-disclaimer">示例价格与库存 · 图片为外观示意</span>
        </div>
        <div className="shop-filters">
          <div className="sort-buttons">
            <button aria-pressed={sort === 'default'} onClick={() => setSort('default')}>
              综合排序
            </button>
            <label>
              <ArrowDownUp size={14} />
              <select
                aria-label="商品排序"
                value={sort}
                onChange={(event) => setSort(event.target.value as ProductSort)}
              >
                <option value="default">价格排序</option>
                <option value="price-asc">价格从低到高</option>
                <option value="price-desc">价格从高到低</option>
              </select>
            </label>
          </div>
          <label className="budget-filter">
            <SlidersHorizontal size={15} />
            <select
              aria-label="商品价格区间"
              value={budget}
              onChange={(event) => setBudget(event.target.value as PriceFilter)}
            >
              <option value="all">全部价格</option>
              <option value="under200">200 元及以下</option>
              <option value="200to400">200–400 元</option>
              <option value="over400">400 元以上</option>
            </select>
          </label>
          <label className="stock-filter">
            <input
              type="checkbox"
              checked={onlyStock}
              onChange={(e) => setOnlyStock(e.target.checked)}
            />
            仅看有货
          </label>
          {(query || category !== '全部商品' || budget !== 'all' || onlyStock) && (
            <button className="text-button clear-filters" onClick={clear}>
              清除筛选
            </button>
          )}
        </div>
        {loading ? (
          <div className="large-empty shop-empty" aria-live="polite">
            <PackageSearch size={40} />
            <h3>正在加载商品</h3>
            <p>正在从商品服务获取最新的价格、库存和规格。</p>
          </div>
        ) : catalogError ? (
          <div className="large-empty shop-empty" role="alert">
            <PackageSearch size={40} />
            <h3>商品加载失败</h3>
            <p>{catalogError}</p>
            <button className="btn" onClick={() => setReloadVersion((version) => version + 1)}>
              重新加载
            </button>
          </div>
        ) : products.length ? (
          <div className="shop-product-grid">
            {products.map((p) => (
              <article
                className={`shop-product ${!p.stock ? 'sold-out' : ''}`}
                data-testid={`shop-product-${p.id}`}
                key={p.id}
              >
                <button
                  className="shop-product-main"
                  aria-label={`查看 ${p.name} 商品详情`}
                  onClick={() => setSelected(p.id)}
                >
                  <div className="shop-photo-wrap">
                    <CatalogPhoto product={p} />
                    <span className="shop-category-tag">{p.category}</span>
                    {!p.stock && <span className="sold-out-label">暂时缺货</span>}
                    <span className="photo-hover">
                      查看商品详情 <ArrowRight size={15} />
                    </span>
                  </div>
                  <div className="shop-product-copy">
                    <div className="shop-price">
                      <span>¥</span>
                      <strong>{p.price}</strong>
                      <small>示例价</small>
                    </div>
                    <h3>{p.name}</h3>
                    <p>{p.description}</p>
                    <div className="shop-spec-tags">
                      {p.specs.slice(0, 2).map((spec) => (
                        <span key={spec}>{spec}</span>
                      ))}
                    </div>
                  </div>
                </button>
                <div className="shop-product-footer">
                  <span className={p.stock ? 'in-stock' : ''}>
                    {p.stock ? `库存 ${p.stock} 件` : '补货时间待定'}
                  </span>
                  <button
                    onClick={() => act(p.id)}
                    aria-label={`${staff ? '引用' : '咨询'} ${p.name}`}
                  >
                    <MessageSquare size={14} />
                    {staff ? '引用商品' : '咨询商品'}
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="large-empty shop-empty">
            <PackageSearch size={40} />
            <h3>没有找到符合条件的商品</h3>
            <p>试试其他关键词，或调整分类和价格区间。</p>
            <button className="btn" onClick={clear}>
              查看全部商品
            </button>
          </div>
        )}
      </section>
      <div className="shop-bottom-note">
        <ShieldCheck size={17} />
        <span>商品、价格与库存均为演示数据。需要具体建议时，可以随时回到客服会话。</span>
      </div>
      {selected && (
        <Modal title="商品详情" onClose={() => setSelected(undefined)}>
          {detailLoading ? (
            <div className="large-empty shop-detail-status" aria-live="polite">
              <PackageSearch size={36} />
              <h3>正在加载商品详情</h3>
            </div>
          ) : detailError ? (
            <div className="large-empty shop-detail-status" role="alert">
              <PackageSearch size={36} />
              <h3>商品详情加载失败</h3>
              <p>{detailError}</p>
              <button className="btn" onClick={() => setSelected(undefined)}>
                关闭
              </button>
            </div>
          ) : selectedProduct ? (
            <>
              <div className="shop-detail">
                <CatalogPhoto product={selectedProduct} />
                <div className="detail-copy">
                  <Badge tone="blue">{selectedProduct.category} · 示例商品</Badge>
                  <h2>{selectedProduct.name}</h2>
                  <p>{selectedProduct.description}</p>
                  <div className="detail-price">
                    {money(selectedProduct.price)}
                    <span>示例价</span>
                  </div>
                  <p className={selectedProduct.stock ? 'in-stock' : 'out-stock'}>
                    {selectedProduct.stock
                      ? `当前库存 ${selectedProduct.stock} 件`
                      : '暂时缺货 · 补货时间待定'}
                  </p>
                  <dl className="detail-specs">
                    <dt>商品编号</dt>
                    <dd>{selectedProduct.id.toUpperCase()}</dd>
                    <dt>系列</dt>
                    <dd>{selectedProduct.series}</dd>
                    {selectedProduct.specs.map((spec, i) => (
                      <div key={spec}>
                        <dt>参数 {i + 1}</dt>
                        <dd>{spec}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              </div>
              <SourceList sources={[selectedProduct.source, 'POL-02 · 售后申请与审核 v1.0']} />
              <div className="detail-service">
                <ShieldCheck size={17} />
                <p>{policies[1].content}</p>
              </div>
              <p className="tiny muted">
                外观为 AI 生成示意图；商品名称、参数和库存以本演示数据为准。
              </p>
              <div className="modal-actions">
                <button className="btn" onClick={() => setSelected(undefined)}>
                  继续逛店
                </button>
                <button className="btn btn-primary" onClick={() => act(selectedProduct.id)}>
                  <MessageSquare size={16} />
                  {staff ? '引用到客服回复' : '咨询这件商品'}
                </button>
              </div>
            </>
          ) : null}
        </Modal>
      )}
    </div>
  );
}
