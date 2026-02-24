# RTL Hierarchy Documentor - 用户指南

RTL层次化文档生成工具，基于Yosys分析RTL设计，生成模块层次树和简化原理图。

## 功能概述

1. **层次树构建** - 从RTL设计提取完整的模块实例层次关系
2. **源码位置标注** - 为PROC块和组合逻辑标注对应的RTL源文件位置
3. **原理图简化** - 将组合逻辑聚合为IN_COMB块，保留PROC和I/O端口

## 环境配置

```bash
# 初始化环境（必须）
source /home/c910/lingzichao/openc910/RTLHierDocor/env.sh
```

环境脚本会：
- 加载 oss-cad-suite 工具链（Yosys）
- 激活包含 pyosys 的 Python 虚拟环境
- 设置 PYTHONPATH

## 命令行使用

### 完整文档生成

```bash
# 从RTL filelist生成（推荐，自动缓存）
python3 -m rtl_hier_docor.cli generate \
    -f /path/to/filelist.f \
    -t top_module_name \
    -o output_dir/ \
    --max-schematics 50

# 从已缓存的RTLIL生成（跳过解析，更快）
python3 -m rtl_hier_docor.cli generate \
    -r /path/to/cached.il \
    -t top_module_name \
    -o output_dir/
```

**输出内容**：
- `hierarchy_tree.txt` - ASCII格式层次树
- `hierarchy_tree.json` - JSON格式完整层次数据
- `index.json` - 模块统计索引
- `schematics/*.dot` - 简化原理图（DOT格式）

### 仅生成层次树

```bash
python3 -m rtl_hier_docor.cli hierarchy \
    -f /path/to/filelist.f \
    -t top_module_name \
    --format ascii  # 或 json
```

### 单个模块原理图

```bash
python3 -m rtl_hier_docor.cli schematic \
    -r /path/to/cached.il \
    -m module_name \
    -o output.dot
```

### 信号连通性检查（afterproc）

给定起点信号和终点信号，检查在 `proc` 后原理图中是否存在路径。

```bash
python3 -m cli connectivity \
    -f /path/to/filelist.f \
    -t top_module_name \
    -m module_name \
    --from-signal start_sig \
    --to-signal end_sig
```

示例（input -> output）：

```bash
python3 -m cli connectivity \
    -r .rtl_cache/c739e0ab984ee48cf26407323de85dc7.il \
    -t openC910 \
    -m ct_ifu_pcgen \
    --from-signal had_ifu_pc \
    --to-signal pcgen_ifctrl_pc
```

可选参数：
- `--undirected`：按无向图检查（默认按有向边）
- `--max-paths`：最多返回多少条 simple path（默认 0，不限）
- `--max-depth`：每条路径最多包含多少个节点（默认 0，不限）
- `-o/--output`：自定义输出 JSON 文件路径

输出：
- 始终输出一个完整 JSON（单行），包含 `exists`、`path_nodes`、`all_path_nodes`、`path_count`、`truncated`、`matched_from_nodes`、`matched_to_nodes` 等字段
- `path_nodes` 保留为兼容字段（第一条见证路径）；`all_path_nodes` 为点对点之间提取到的全部路径列表
- `path_conditions` 按路径返回条件点（每条路径一个条件集合）；`conditions` 为去重后的全量条件点集合
- 默认同时写入：`<output_dir>/pathcheck/<module>__<from>__to__<to>.json`
- 连通性按“数据路径”检查：会过滤控制边（如 `$mux/$procmux` 的 `S/SEL` 输入、时序单元的 `CLK/reset/EN` 等控制端口输入），避免把控制信号误判为数据可达
- 条件传播点在 `conditions` 字段中返回：
    - `$mux/$pmux/$procmux`：记录选择端口（`S/SEL/S*`）
    - 时序单元（如 `$adff/$dff`）：仅在存在数据使能端口（`EN/CE/LOAD/GATE`）时记录；`CLK/reset` 不计入 condition 点
- 若可解析到 cell 源信息，`conditions[*].source_locations` 会给出对应 RTL 文件与行号（优先来自 cell 的 `src` 属性；必要时回退到 `proc` 日志映射）

返回码：
- `0`：存在路径
- `1`：不存在路径或执行失败

## 输入格式

### Filelist 格式

标准Verilog filelist，支持 `-f` 嵌套引用：

```
// 注释
+incdir+/path/to/includes
-f sub_filelist.f
/path/to/module1.v
/path/to/module2.sv
```

### RTLIL 缓存

系统会自动根据filelist内容的MD5哈希管理缓存，存放在工作目录的 `.rtl_index_cache/` 下。

## 输出格式说明

### 层次树 (hierarchy_tree.txt)

```
[ROOT] openC910 (openC910)
├── x_ct_had_common_top (ct_had_common_top)
│   ├── x_ct_had_common_dbg_info (ct_had_common_dbg_info)
│   │   └── x_dbginfo_clk (gated_clk_cell)
│   └── x_ct_had_common_regs (ct_had_common_regs)
└── x_ct_sysio_top (ct_sysio_top)
    └── x_ct_sysio_core1 (ct_sysio_kid)
```

格式：`实例名 (模块类型)`

### 简化原理图 (DOT)

原理图包含三类节点：

| 节点类型 | 形状 | 说明 |
|----------|------|------|
| I/O端口 | octagon | 模块输入/输出端口 |
| PROC块 | box (rounded) | 时序逻辑，标注源码位置 |
| IN_COMB | box (filled, lightblue) | 聚合的组合逻辑 |

**PROC块标注示例**：
```dot
p528 [shape=box, style=rounded, 
      label="PROC $14231\n/path/to/file.v:2556.1-2567.4"];
```

**IN_COMB块标注示例**：
```dot
_no_proc_in_comb_0 [shape=box, style="filled,rounded", 
                    fillcolor=lightblue,
                    label="IN_COMB\n(752)\nfile.v:627-1298"];
```

括号内数字为聚合的cell数量，下方为源码行号范围。

### 渲染原理图

```bash
# 生成PNG
dot -Tpng schematics/ct_fadd_double_dp.dot -o ct_fadd_double_dp.png

# 生成SVG（推荐，可缩放）
dot -Tsvg schematics/ct_fadd_double_dp.dot -o ct_fadd_double_dp.svg
```

## 架构设计

```
src/rtl_hier_docor/
├── models/              # 数据模型
│   ├── source_loc.py    # SourceLocation, AggregatedLocation
│   ├── module.py        # ModuleInfo, PortInfo, CellInfo
│   └── hierarchy.py     # HierarchyNode 层次树节点
├── core/                # 核心处理
│   ├── yosys_backend.py # Yosys/pyosys封装，filelist解析，缓存
│   ├── hierarchy_builder.py  # 层次树构建
│   └── source_extractor.py   # 源码位置提取
├── schematic/           # 原理图生成
│   ├── generator.py     # 调用Yosys show命令
│   └── simplifier.py    # DOT解析和组合逻辑简化
├── output/              # 输出生成
│   └── generator.py     # 文档输出编排
└── cli.py               # 命令行入口
```

### 核心类说明

| 类 | 职责 |
|----|------|
| `YosysBackend` | 封装pyosys，管理RTL加载和缓存 |
| `HierarchyBuilder` | 遍历设计构建HierarchyNode树 |
| `SourceExtractor` | 从RTLIL提取cell的src属性 |
| `DotSimplifier` | 解析DOT，聚合组合逻辑，生成简化图 |
| `DocumentGenerator` | 编排完整文档生成流程 |

### 简化算法

1. **解析** - 正则提取DOT中的节点和边
2. **分类** - 按shape/style将节点分为PROC、I/O、组合逻辑等
3. **分组** - 将无PROC关联的组合逻辑按连通性分组
4. **聚合** - 每组生成一个IN_COMB节点，聚合源码位置
5. **重连** - 保留边界连接（到PROC和I/O的边）

## 示例：C910 CPU

```bash
source env.sh

# 完整生成
python3 -m cli generate \
    -f /home/c910/lingzichao/openc910/C910_RTL_FACTORY/gen_rtl/filelists/C910_asic_rtl.fl \
    -t openC910 \
    -o output/

# 查看结果
head -100 output/hierarchy_tree.txt
dot -Tsvg output/schematics/ct_idu_id_decd.dot -o ct_idu_id_decd.svg
```

## 已知限制

1. 部分大型模块（如 ct_ifu_lbuf）因递归深度限制可能生成失败
2. 参数化模块（$paramod）默认跳过
3. 空模块（无logic cell）不生成原理图

## 依赖

- Python 3.8+
- Yosys (via oss-cad-suite)
- pyosys (oss-cad-suite内置)
- Graphviz (可选，用于渲染DOT)
