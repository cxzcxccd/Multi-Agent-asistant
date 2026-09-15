# 商品陈列素材记录

- 生成方式：内置 `image_gen.imagegen`，使用 imagegen 技能；未使用 CLI fallback。
- 项目文件：`frontend/public/images/catalog-products.png`。
- 本地绝对路径：`D:/Agent/Multi-Agent-asistant/frontend/public/images/catalog-products.png`。
- 用途：数码旗舰店商品网格与详情页中的外观示意。
- 格式：一个 4 列 × 3 行的图片图集，按 `data.ts` 中 p01–p12 的顺序排列；通过 CSS 背景定位显示各商品，无需额外裁图。
- 原始文件保留在 imagegen 默认输出目录，选定图片已复制进项目。
- 商品卡片及详情标明为示例商品，图像不用于证明真实商品外观、品牌或参数。

## 完整生成提示词

```text
Use case: product-mockup. Asset type: a SINGLE ecommerce product catalog sprite sheet for a fictional digital accessories shop. Generate one rectangular image, exactly 4 columns by 3 rows of equal square cells, aspect ratio 4:3, ideally 2048x1536. Each cell has the SAME very pale cool gray studio background #f4f6fa. No grid lines, no borders, no text, no letters, no logos, no watermarks. Every object is fully inside its cell with at least 12 percent clear margin, centered, clean commercial studio product photography, soft natural shadows, consistent camera angle. Each cell contains only one standalone product or natural product set (earbuds plus case). EXACT row-major arrangement: first row cell 1 white in-ear wireless earbuds with open white charging case; cell 2 premium navy blue over-ear headphones; cell 3 pale mint semi-in-ear wireless earbuds and matching compact case; cell 4 charcoal athletic wireless ear-hook earbuds and case. Second row cell 1 compact white rounded rectangular wall charger with two USB-C ports; cell 2 dark graphite rectangular desktop charger with three USB ports; cell 3 tiny pale mint single USB-C white wall charger; cell 4 premium charcoal rectangular travel wall charger with two USB-C ports and folded prongs. Third row cell 1 silver aluminum slim six-in-one USB-C hub with short attached cable; cell 2 dark gray nine-in-one USB-C hub with short attached cable and several distinct side ports; cell 3 small pale silver four-port USB data hub with short cable; cell 4 dark graphite desktop dual-display docking station with separate power socket and multiple visible ports. Products are fictitious illustrations, not exact copies of real branded goods. Do not merge the cells into a still life. Perfectly regular equal-size 4 by 3 matrix is crucial for CSS sprite positioning. No interface chrome, no prices or typography.
```
