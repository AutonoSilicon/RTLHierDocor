# RV64GC Instruction Datasheet

## Scope

- 覆盖范围: `RV64GC = RV64I + M + A + F + D + C + Zicsr + Zifencei`。
- 本文严格采用 datasheet 形态组织: 每条标准指令都以三级标题 `###` 单独列出。
- RV Spec 参考: `https://docs.riscv.org/reference/isa/unpriv/rv32.html#2-10-hint-instructions`。
- `inst bit range` 使用 `inst[x:y]` 方式标注编码位段; 对被打散的立即数，直接标出其在指令中的拼接来源。
- `类汇编软件表达式` 用接近软件/验证环境可读的伪代码表示。
- 不收录伪指令和特权指令，例如 `MV`、`RET`、`MRET`、`SRET`、`WFI`。

## Notation

- `x[rd]`: 整数寄存器写回。
- `f[rd]`: 浮点寄存器写回。
- `pc`: 当前指令地址。
- `M[a]`: 内存地址 `a` 处的内容。
- `sext(v)`: 符号扩展。
- `zext(v)`: 零扩展。
- `SEXT32(v)`: 仅保留 `v` 低 32 位，并将 bit31 符号扩展到 64 位。
- `rm`: 浮点舍入模式; 若编码为 `111`，则使用 `fcsr.frm`。
- `rs1'`、`rs2'`、`rd'`: 压缩指令中的 3-bit 压缩寄存器索引，对应 `x8-x15` 或 `f8-f15`。

## RV64I Base Integer Instructions

### LUI rd, imm20

- 指令形式: `LUI rd, imm20`
- 所属扩展: `RV64I`
- 编码格式: `U-type`
- 类汇编软件表达式: `x[rd] = sext(imm20 << 12)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:12]` | `imm[31:12]` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110111` |

- 详细说明:
  - 将 20 位高立即数装入目的寄存器，高 20 位来自指令，低 12 位自动补 0。
  - 在 RV64 中，写回结果会按 32 位值再符号扩展到 64 位。
  - 常用于与 `ADDI`/`LD`/`JALR` 组合构造常量或绝对地址。

### AUIPC rd, imm20

- 指令形式: `AUIPC rd, imm20`
- 所属扩展: `RV64I`
- 编码格式: `U-type`
- 类汇编软件表达式: `x[rd] = pc + sext(imm20 << 12)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:12]` | `imm[31:12]` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0010111` |

- 详细说明:
  - 以当前指令地址 `pc` 为基址加上高位立即数偏移。
  - 常与 `JALR` 组合做远距离 PC-relative 跳转，或与 `LD/SD` 组合做 PC-relative 数据访问。

### JAL rd, imm20

- 指令形式: `JAL rd, imm20`
- 所属扩展: `RV64I`
- 编码格式: `J-type`
- 类汇编软件表达式: `tmp = pc + 4; pc = pc + sext(imm[20|10:1|11|19:12] << 1); x[rd] = tmp`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31]` | `imm[20]` |
| `inst[30:21]` | `imm[10:1]` |
| `inst[20]` | `imm[11]` |
| `inst[19:12]` | `imm[19:12]` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1101111` |

- 详细说明:
  - 执行 PC-relative 无条件跳转，并把返回地址 `pc+4` 写入 `rd`。
  - 当 `rd=x0` 时，相当于纯跳转。
  - 跳转目标按 2 字节对齐编码。

### JALR rd, rs1, imm12

- 指令形式: `JALR rd, rs1, imm12`
- 所属扩展: `RV64I`
- 编码格式: `I-type`
- 类汇编软件表达式: `tmp = pc + 4; pc = (x[rs1] + sext(imm12)) & ~1; x[rd] = tmp`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `imm[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1100111` |

- 详细说明:
  - 执行寄存器间接跳转。
  - 目标地址最低位会被硬件清零，以简化实现并允许最低位作为软件标记位。
  - 常用于函数返回、间接调用和通过 `LUI/AUIPC` 构造的长跳转。

### BEQ rs1, rs2, offset

- 指令形式: `BEQ rs1, rs2, offset`
- 所属扩展: `RV64I`
- 编码格式: `B-type`
- 类汇编软件表达式: `if x[rs1] == x[rs2]: pc = pc + sext(bimm << 1)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31]` | `imm[12]` |
| `inst[30:25]` | `imm[10:5]` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:8]` | `imm[4:1]` |
| `inst[7]` | `imm[11]` |
| `inst[6:0]` | `1100011` |

- 详细说明:
  - 比较 `rs1` 与 `rs2`，满足“相等”条件时跳转。
  - 分支偏移按 2 字节单位编码，最终会符号扩展后加到当前 `pc` 上。
  - 仅当分支实际 taken 时才可能触发目标地址对齐异常。

### BNE rs1, rs2, offset

- 指令形式: `BNE rs1, rs2, offset`
- 所属扩展: `RV64I`
- 编码格式: `B-type`
- 类汇编软件表达式: `if x[rs1] != x[rs2]: pc = pc + sext(bimm << 1)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31]` | `imm[12]` |
| `inst[30:25]` | `imm[10:5]` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `001` |
| `inst[11:8]` | `imm[4:1]` |
| `inst[7]` | `imm[11]` |
| `inst[6:0]` | `1100011` |

- 详细说明:
  - 比较 `rs1` 与 `rs2`，满足“不相等”条件时跳转。
  - 分支偏移按 2 字节单位编码，最终会符号扩展后加到当前 `pc` 上。
  - 仅当分支实际 taken 时才可能触发目标地址对齐异常。

### BLT rs1, rs2, offset

- 指令形式: `BLT rs1, rs2, offset`
- 所属扩展: `RV64I`
- 编码格式: `B-type`
- 类汇编软件表达式: `if s(x[rs1]) < s(x[rs2]): pc = pc + sext(bimm << 1)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31]` | `imm[12]` |
| `inst[30:25]` | `imm[10:5]` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `100` |
| `inst[11:8]` | `imm[4:1]` |
| `inst[7]` | `imm[11]` |
| `inst[6:0]` | `1100011` |

- 详细说明:
  - 比较 `rs1` 与 `rs2`，满足“有符号小于”条件时跳转。
  - 分支偏移按 2 字节单位编码，最终会符号扩展后加到当前 `pc` 上。
  - 仅当分支实际 taken 时才可能触发目标地址对齐异常。

### BGE rs1, rs2, offset

- 指令形式: `BGE rs1, rs2, offset`
- 所属扩展: `RV64I`
- 编码格式: `B-type`
- 类汇编软件表达式: `if s(x[rs1]) >= s(x[rs2]): pc = pc + sext(bimm << 1)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31]` | `imm[12]` |
| `inst[30:25]` | `imm[10:5]` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `101` |
| `inst[11:8]` | `imm[4:1]` |
| `inst[7]` | `imm[11]` |
| `inst[6:0]` | `1100011` |

- 详细说明:
  - 比较 `rs1` 与 `rs2`，满足“有符号大于等于”条件时跳转。
  - 分支偏移按 2 字节单位编码，最终会符号扩展后加到当前 `pc` 上。
  - 仅当分支实际 taken 时才可能触发目标地址对齐异常。

### BLTU rs1, rs2, offset

- 指令形式: `BLTU rs1, rs2, offset`
- 所属扩展: `RV64I`
- 编码格式: `B-type`
- 类汇编软件表达式: `if u(x[rs1]) < u(x[rs2]): pc = pc + sext(bimm << 1)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31]` | `imm[12]` |
| `inst[30:25]` | `imm[10:5]` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `110` |
| `inst[11:8]` | `imm[4:1]` |
| `inst[7]` | `imm[11]` |
| `inst[6:0]` | `1100011` |

- 详细说明:
  - 比较 `rs1` 与 `rs2`，满足“无符号小于”条件时跳转。
  - 分支偏移按 2 字节单位编码，最终会符号扩展后加到当前 `pc` 上。
  - 仅当分支实际 taken 时才可能触发目标地址对齐异常。

### BGEU rs1, rs2, offset

- 指令形式: `BGEU rs1, rs2, offset`
- 所属扩展: `RV64I`
- 编码格式: `B-type`
- 类汇编软件表达式: `if u(x[rs1]) >= u(x[rs2]): pc = pc + sext(bimm << 1)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31]` | `imm[12]` |
| `inst[30:25]` | `imm[10:5]` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `111` |
| `inst[11:8]` | `imm[4:1]` |
| `inst[7]` | `imm[11]` |
| `inst[6:0]` | `1100011` |

- 详细说明:
  - 比较 `rs1` 与 `rs2`，满足“无符号大于等于”条件时跳转。
  - 分支偏移按 2 字节单位编码，最终会符号扩展后加到当前 `pc` 上。
  - 仅当分支实际 taken 时才可能触发目标地址对齐异常。

### LB rd, imm12(rs1)

- 指令形式: `LB rd, imm12(rs1)`
- 所属扩展: `RV64I`
- 编码格式: `I-type`
- 类汇编软件表达式: `x[rd] = sext(M[x[rs1] + sext(imm12)][7:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `imm[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0000011` |

- 详细说明:
  - 从内存读取 1 字节并做符号扩展。
  - 有效地址由 `x[rs1] + sext(imm12)` 形成。
  - 自然对齐的 8-bit 访问不得产生地址未对齐异常；非对齐访问行为由 EEI 决定。

### LH rd, imm12(rs1)

- 指令形式: `LH rd, imm12(rs1)`
- 所属扩展: `RV64I`
- 编码格式: `I-type`
- 类汇编软件表达式: `x[rd] = sext(M[x[rs1] + sext(imm12)][15:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `imm[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `001` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0000011` |

- 详细说明:
  - 从内存读取 2 字节并做符号扩展。
  - 有效地址由 `x[rs1] + sext(imm12)` 形成。
  - 自然对齐的 16-bit 访问不得产生地址未对齐异常；非对齐访问行为由 EEI 决定。

### LW rd, imm12(rs1)

- 指令形式: `LW rd, imm12(rs1)`
- 所属扩展: `RV64I`
- 编码格式: `I-type`
- 类汇编软件表达式: `x[rd] = sext(M[x[rs1] + sext(imm12)][31:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `imm[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0000011` |

- 详细说明:
  - 从内存读取 4 字节并符号扩展到 64 位。
  - 有效地址由 `x[rs1] + sext(imm12)` 形成。
  - 自然对齐的 32-bit 访问不得产生地址未对齐异常；非对齐访问行为由 EEI 决定。

### LBU rd, imm12(rs1)

- 指令形式: `LBU rd, imm12(rs1)`
- 所属扩展: `RV64I`
- 编码格式: `I-type`
- 类汇编软件表达式: `x[rd] = zext(M[x[rs1] + sext(imm12)][7:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `imm[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `100` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0000011` |

- 详细说明:
  - 从内存读取 1 字节并做零扩展。
  - 有效地址由 `x[rs1] + sext(imm12)` 形成。
  - 自然对齐的 8-bit 访问不得产生地址未对齐异常；非对齐访问行为由 EEI 决定。

### LHU rd, imm12(rs1)

- 指令形式: `LHU rd, imm12(rs1)`
- 所属扩展: `RV64I`
- 编码格式: `I-type`
- 类汇编软件表达式: `x[rd] = zext(M[x[rs1] + sext(imm12)][15:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `imm[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `101` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0000011` |

- 详细说明:
  - 从内存读取 2 字节并做零扩展。
  - 有效地址由 `x[rs1] + sext(imm12)` 形成。
  - 自然对齐的 16-bit 访问不得产生地址未对齐异常；非对齐访问行为由 EEI 决定。

### LWU rd, imm12(rs1)

- 指令形式: `LWU rd, imm12(rs1)`
- 所属扩展: `RV64I`
- 编码格式: `I-type`
- 类汇编软件表达式: `x[rd] = zext(M[x[rs1] + sext(imm12)][31:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `imm[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `110` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0000011` |

- 详细说明:
  - 从内存读取 4 字节并做零扩展到 64 位。
  - 有效地址由 `x[rs1] + sext(imm12)` 形成。
  - 自然对齐的 32-bit 访问不得产生地址未对齐异常；非对齐访问行为由 EEI 决定。

### LD rd, imm12(rs1)

- 指令形式: `LD rd, imm12(rs1)`
- 所属扩展: `RV64I`
- 编码格式: `I-type`
- 类汇编软件表达式: `x[rd] = M[x[rs1] + sext(imm12)][63:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `imm[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0000011` |

- 详细说明:
  - 从内存读取 8 字节。
  - 有效地址由 `x[rs1] + sext(imm12)` 形成。
  - 自然对齐的 64-bit 访问不得产生地址未对齐异常；非对齐访问行为由 EEI 决定。

### SB rs2, imm12(rs1)

- 指令形式: `SB rs2, imm12(rs1)`
- 所属扩展: `RV64I`
- 编码格式: `S-type`
- 类汇编软件表达式: `M[x[rs1] + sext(imm12)][7:0] = x[rs2][7:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `imm[11:5]` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `imm[4:0]` |
| `inst[6:0]` | `0100011` |

- 详细说明:
  - 把 `rs2` 低 8 位写入内存。
  - 有效地址由 `x[rs1] + sext(imm12)` 形成。
  - 自然对齐的 8-bit 写访问保证可用；非对齐访问是否允许由执行环境定义。

### SH rs2, imm12(rs1)

- 指令形式: `SH rs2, imm12(rs1)`
- 所属扩展: `RV64I`
- 编码格式: `S-type`
- 类汇编软件表达式: `M[x[rs1] + sext(imm12)][15:0] = x[rs2][15:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `imm[11:5]` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `001` |
| `inst[11:7]` | `imm[4:0]` |
| `inst[6:0]` | `0100011` |

- 详细说明:
  - 把 `rs2` 低 16 位写入内存。
  - 有效地址由 `x[rs1] + sext(imm12)` 形成。
  - 自然对齐的 16-bit 写访问保证可用；非对齐访问是否允许由执行环境定义。

### SW rs2, imm12(rs1)

- 指令形式: `SW rs2, imm12(rs1)`
- 所属扩展: `RV64I`
- 编码格式: `S-type`
- 类汇编软件表达式: `M[x[rs1] + sext(imm12)][31:0] = x[rs2][31:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `imm[11:5]` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `imm[4:0]` |
| `inst[6:0]` | `0100011` |

- 详细说明:
  - 把 `rs2` 低 32 位写入内存。
  - 有效地址由 `x[rs1] + sext(imm12)` 形成。
  - 自然对齐的 32-bit 写访问保证可用；非对齐访问是否允许由执行环境定义。

### SD rs2, imm12(rs1)

- 指令形式: `SD rs2, imm12(rs1)`
- 所属扩展: `RV64I`
- 编码格式: `S-type`
- 类汇编软件表达式: `M[x[rs1] + sext(imm12)][63:0] = x[rs2][63:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `imm[11:5]` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `imm[4:0]` |
| `inst[6:0]` | `0100011` |

- 详细说明:
  - 把 `rs2` 低 64 位写入内存。
  - 有效地址由 `x[rs1] + sext(imm12)` 形成。
  - 自然对齐的 64-bit 写访问保证可用；非对齐访问是否允许由执行环境定义。

### ADDI rd, rs1, imm12

- 指令形式: `ADDI rd, rs1, imm12`
- 所属扩展: `RV64I`
- 编码格式: `I-type`
- 类汇编软件表达式: `x[rd] = x[rs1] + sext(imm12)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `imm[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0010011` |

- 详细说明:
  - 整数加立即数，不对溢出产生异常。
  - 立即数字段在解码后会统一做符号扩展。

### SLTI rd, rs1, imm12

- 指令形式: `SLTI rd, rs1, imm12`
- 所属扩展: `RV64I`
- 编码格式: `I-type`
- 类汇编软件表达式: `x[rd] = 1 if s(x[rs1]) < s(sext(imm12)) else 0`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `imm[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0010011` |

- 详细说明:
  - 有符号立即数比较，小于时写 1。
  - 立即数字段在解码后会统一做符号扩展。

### SLTIU rd, rs1, imm12

- 指令形式: `SLTIU rd, rs1, imm12`
- 所属扩展: `RV64I`
- 编码格式: `I-type`
- 类汇编软件表达式: `x[rd] = 1 if u(x[rs1]) < u(sext(imm12)) else 0`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `imm[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0010011` |

- 详细说明:
  - 无符号立即数比较，小于时写 1。
  - 目的寄存器始终为 `rd`。
  - `SLTIU rd, rs1, 1` 常被汇编器用来实现 `SEQZ`。

### XORI rd, rs1, imm12

- 指令形式: `XORI rd, rs1, imm12`
- 所属扩展: `RV64I`
- 编码格式: `I-type`
- 类汇编软件表达式: `x[rd] = x[rs1] ^ sext(imm12)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `imm[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `100` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0010011` |

- 详细说明:
  - 与立即数做按位异或。
  - 立即数字段在解码后会统一做符号扩展。

### ORI rd, rs1, imm12

- 指令形式: `ORI rd, rs1, imm12`
- 所属扩展: `RV64I`
- 编码格式: `I-type`
- 类汇编软件表达式: `x[rd] = x[rs1] | sext(imm12)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `imm[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `110` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0010011` |

- 详细说明:
  - 与立即数做按位或。
  - 立即数字段在解码后会统一做符号扩展。

### ANDI rd, rs1, imm12

- 指令形式: `ANDI rd, rs1, imm12`
- 所属扩展: `RV64I`
- 编码格式: `I-type`
- 类汇编软件表达式: `x[rd] = x[rs1] & sext(imm12)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `imm[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `111` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0010011` |

- 详细说明:
  - 与立即数做按位与。
  - 立即数字段在解码后会统一做符号扩展。

### SLLI rd, rs1, shamt

- 指令形式: `SLLI rd, rs1, shamt`
- 所属扩展: `RV64I`
- 编码格式: `I-type (shift-immediate)`
- 类汇编软件表达式: `x[rd] = x[rs1] << shamt`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:26]` | `000000` |
| `inst[25:20]` | `shamt[5:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `001` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0010011` |

- 详细说明:
  - 逻辑左移立即数，RV64 使用 6-bit 移位量。
  - 移位量字段直接编码在立即数字段中。

### SRLI rd, rs1, shamt

- 指令形式: `SRLI rd, rs1, shamt`
- 所属扩展: `RV64I`
- 编码格式: `I-type (shift-immediate)`
- 类汇编软件表达式: `x[rd] = u(x[rs1]) >> shamt`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:26]` | `000000` |
| `inst[25:20]` | `shamt[5:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `101` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0010011` |

- 详细说明:
  - 逻辑右移立即数，高位补 0。
  - 移位量字段直接编码在立即数字段中。

### SRAI rd, rs1, shamt

- 指令形式: `SRAI rd, rs1, shamt`
- 所属扩展: `RV64I`
- 编码格式: `I-type (shift-immediate)`
- 类汇编软件表达式: `x[rd] = s(x[rs1]) >> shamt`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:26]` | `010000` |
| `inst[25:20]` | `shamt[5:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `101` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0010011` |

- 详细说明:
  - 算术右移立即数，高位补符号位。
  - 移位量字段直接编码在立即数字段中。

### ADDIW rd, rs1, imm12

- 指令形式: `ADDIW rd, rs1, imm12`
- 所属扩展: `RV64I`
- 编码格式: `I-type`
- 类汇编软件表达式: `x[rd] = SEXT32(x[rs1] + sext(imm12))`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `imm[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0011011` |

- 详细说明:
  - 对低 32 位做加立即数，再符号扩展到 64 位。
  - 常用 `ADDIW rd, rs1, 0` 实现 `SEXT.W`。

### SLLIW rd, rs1, shamt

- 指令形式: `SLLIW rd, rs1, shamt`
- 所属扩展: `RV64I`
- 编码格式: `I-type (W shift-immediate)`
- 类汇编软件表达式: `x[rd] = SEXT32(x[rs1][31:0] << shamt[4:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000000` |
| `inst[24:20]` | `shamt[4:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `001` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0011011` |

- 详细说明:
  - 对低 32 位做逻辑左移后符号扩展。
  - 该类指令仅处理源操作数低 32 位；结果总会以 32 位符号扩展形式写回。

### SRLIW rd, rs1, shamt

- 指令形式: `SRLIW rd, rs1, shamt`
- 所属扩展: `RV64I`
- 编码格式: `I-type (W shift-immediate)`
- 类汇编软件表达式: `x[rd] = SEXT32(u(x[rs1][31:0]) >> shamt[4:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000000` |
| `inst[24:20]` | `shamt[4:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `101` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0011011` |

- 详细说明:
  - 对低 32 位做逻辑右移后符号扩展。
  - 该类指令仅处理源操作数低 32 位；结果总会以 32 位符号扩展形式写回。

### SRAIW rd, rs1, shamt

- 指令形式: `SRAIW rd, rs1, shamt`
- 所属扩展: `RV64I`
- 编码格式: `I-type (W shift-immediate)`
- 类汇编软件表达式: `x[rd] = SEXT32(s(x[rs1][31:0]) >> shamt[4:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0100000` |
| `inst[24:20]` | `shamt[4:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `101` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0011011` |

- 详细说明:
  - 对低 32 位做算术右移后符号扩展。
  - 该类指令仅处理源操作数低 32 位；结果总会以 32 位符号扩展形式写回。

### ADD rd, rs1, rs2

- 指令形式: `ADD rd, rs1, rs2`
- 所属扩展: `RV64I`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = x[rs1] + x[rs2]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110011` |

- 详细说明:
  - 整数加法，忽略溢出。

### SUB rd, rs1, rs2

- 指令形式: `SUB rd, rs1, rs2`
- 所属扩展: `RV64I`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = x[rs1] - x[rs2]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0100000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110011` |

- 详细说明:
  - 整数减法，忽略溢出。

### SLL rd, rs1, rs2

- 指令形式: `SLL rd, rs1, rs2`
- 所属扩展: `RV64I`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = x[rs1] << x[rs2][5:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `001` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110011` |

- 详细说明:
  - 逻辑左移，RV64 只使用 `rs2` 低 6 位作为移位量。

### SLT rd, rs1, rs2

- 指令形式: `SLT rd, rs1, rs2`
- 所属扩展: `RV64I`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = 1 if s(x[rs1]) < s(x[rs2]) else 0`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110011` |

- 详细说明:
  - 有符号比较，小于写 1。

### SLTU rd, rs1, rs2

- 指令形式: `SLTU rd, rs1, rs2`
- 所属扩展: `RV64I`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = 1 if u(x[rs1]) < u(x[rs2]) else 0`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110011` |

- 详细说明:
  - 无符号比较，小于写 1。

### XOR rd, rs1, rs2

- 指令形式: `XOR rd, rs1, rs2`
- 所属扩展: `RV64I`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = x[rs1] ^ x[rs2]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `100` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110011` |

- 详细说明:
  - 按位异或。

### SRL rd, rs1, rs2

- 指令形式: `SRL rd, rs1, rs2`
- 所属扩展: `RV64I`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = u(x[rs1]) >> x[rs2][5:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `101` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110011` |

- 详细说明:
  - 逻辑右移。

### SRA rd, rs1, rs2

- 指令形式: `SRA rd, rs1, rs2`
- 所属扩展: `RV64I`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = s(x[rs1]) >> x[rs2][5:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0100000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `101` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110011` |

- 详细说明:
  - 算术右移。

### OR rd, rs1, rs2

- 指令形式: `OR rd, rs1, rs2`
- 所属扩展: `RV64I`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = x[rs1] | x[rs2]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `110` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110011` |

- 详细说明:
  - 按位或。

### AND rd, rs1, rs2

- 指令形式: `AND rd, rs1, rs2`
- 所属扩展: `RV64I`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = x[rs1] & x[rs2]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `111` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110011` |

- 详细说明:
  - 按位与。

### ADDW rd, rs1, rs2

- 指令形式: `ADDW rd, rs1, rs2`
- 所属扩展: `RV64I`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = SEXT32(x[rs1][31:0] + x[rs2][31:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0111011` |

- 详细说明:
  - 对低 32 位加法后符号扩展。

### SUBW rd, rs1, rs2

- 指令形式: `SUBW rd, rs1, rs2`
- 所属扩展: `RV64I`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = SEXT32(x[rs1][31:0] - x[rs2][31:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0100000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0111011` |

- 详细说明:
  - 对低 32 位减法后符号扩展。

### SLLW rd, rs1, rs2

- 指令形式: `SLLW rd, rs1, rs2`
- 所属扩展: `RV64I`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = SEXT32(x[rs1][31:0] << x[rs2][4:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `001` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0111011` |

- 详细说明:
  - 对低 32 位逻辑左移后符号扩展。

### SRLW rd, rs1, rs2

- 指令形式: `SRLW rd, rs1, rs2`
- 所属扩展: `RV64I`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = SEXT32(u(x[rs1][31:0]) >> x[rs2][4:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `101` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0111011` |

- 详细说明:
  - 对低 32 位逻辑右移后符号扩展。

### SRAW rd, rs1, rs2

- 指令形式: `SRAW rd, rs1, rs2`
- 所属扩展: `RV64I`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = SEXT32(s(x[rs1][31:0]) >> x[rs2][4:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0100000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `101` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0111011` |

- 详细说明:
  - 对低 32 位算术右移后符号扩展。

### FENCE pred, succ

- 指令形式: `FENCE pred, succ`
- 所属扩展: `RV64I`
- 编码格式: `I-type (fence)`
- 类汇编软件表达式: `order(pred -> succ)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:28]` | `fm` |
| `inst[27:24]` | `pred` |
| `inst[23:20]` | `succ` |
| `inst[19:15]` | `rs1 (normally 00000)` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd (normally 00000)` |
| `inst[6:0]` | `0001111` |

- 详细说明:
  - 建立 predecessor 集合与 successor 集合之间的可观察顺序。
  - 既可约束内存访问，也可约束设备 I/O 访问。

### ECALL

- 指令形式: `ECALL`
- 所属扩展: `RV64I`
- 编码格式: `I-type (system)`
- 类汇编软件表达式: `raise EnvironmentCall`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `000000000000` |
| `inst[19:15]` | `00000` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `00000` |
| `inst[6:0]` | `1110011` |

- 详细说明:
  - 触发环境调用异常。
  - 服务号与参数的传递方式由 ABI 或执行环境约定。

### EBREAK

- 指令形式: `EBREAK`
- 所属扩展: `RV64I`
- 编码格式: `I-type (system)`
- 类汇编软件表达式: `raise Breakpoint`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `000000000001` |
| `inst[19:15]` | `00000` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `00000` |
| `inst[6:0]` | `1110011` |

- 详细说明:
  - 触发断点异常。
  - 通常由调试器或 semihosting 机制消费。

## Zifencei

### FENCE.I

- 指令形式: `FENCE.I`
- 所属扩展: `Zifencei`
- 编码格式: `I-type (fence)`
- 类汇编软件表达式: `sync_i_fetch_with_prior_stores()`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `000000000000` |
| `inst[19:15]` | `00000` |
| `inst[14:12]` | `001` |
| `inst[11:7]` | `00000` |
| `inst[6:0]` | `0001111` |

- 详细说明:
  - 同步当前 hart 的数据流和取指流。
  - 保证之前对指令存储区的写入，在本 hart 后续取指时可见。

## Zicsr

### CSRRW rd, csr, rs1

- 指令形式: `CSRRW rd, csr, rs1`
- 所属扩展: `Zicsr`
- 编码格式: `I-type (CSR)`
- 类汇编软件表达式: `t = CSR[csr]; CSR[csr] = x[rs1]; x[rd] = zext(t)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `csr[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `001` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1110011` |

- 详细说明:
  - 原子交换 CSR 与通用寄存器值。
  - CSR 地址位于 `inst[31:20]`。

### CSRRS rd, csr, rs1

- 指令形式: `CSRRS rd, csr, rs1`
- 所属扩展: `Zicsr`
- 编码格式: `I-type (CSR)`
- 类汇编软件表达式: `t = CSR[csr]; CSR[csr] = t | x[rs1]; x[rd] = zext(t)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `csr[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1110011` |

- 详细说明:
  - 读取 CSR，并按掩码置位。
  - CSR 地址位于 `inst[31:20]`。

### CSRRC rd, csr, rs1

- 指令形式: `CSRRC rd, csr, rs1`
- 所属扩展: `Zicsr`
- 编码格式: `I-type (CSR)`
- 类汇编软件表达式: `t = CSR[csr]; CSR[csr] = t & ~x[rs1]; x[rd] = zext(t)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `csr[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1110011` |

- 详细说明:
  - 读取 CSR，并按掩码清位。
  - CSR 地址位于 `inst[31:20]`。

### CSRRWI rd, csr, uimm

- 指令形式: `CSRRWI rd, csr, uimm`
- 所属扩展: `Zicsr`
- 编码格式: `I-type (CSR)`
- 类汇编软件表达式: `t = CSR[csr]; CSR[csr] = zext(uimm); x[rd] = zext(t)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `csr[11:0]` |
| `inst[19:15]` | `rs1/uimm[4:0]` |
| `inst[14:12]` | `101` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1110011` |

- 详细说明:
  - 读取 CSR，并写入 5-bit 零扩展立即数。
  - CSR 地址位于 `inst[31:20]`。

### CSRRSI rd, csr, uimm

- 指令形式: `CSRRSI rd, csr, uimm`
- 所属扩展: `Zicsr`
- 编码格式: `I-type (CSR)`
- 类汇编软件表达式: `t = CSR[csr]; CSR[csr] = t | zext(uimm); x[rd] = zext(t)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `csr[11:0]` |
| `inst[19:15]` | `rs1/uimm[4:0]` |
| `inst[14:12]` | `110` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1110011` |

- 详细说明:
  - 读取 CSR，并按立即数掩码置位。
  - CSR 地址位于 `inst[31:20]`。

### CSRRCI rd, csr, uimm

- 指令形式: `CSRRCI rd, csr, uimm`
- 所属扩展: `Zicsr`
- 编码格式: `I-type (CSR)`
- 类汇编软件表达式: `t = CSR[csr]; CSR[csr] = t & ~zext(uimm); x[rd] = zext(t)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `csr[11:0]` |
| `inst[19:15]` | `rs1/uimm[4:0]` |
| `inst[14:12]` | `111` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1110011` |

- 详细说明:
  - 读取 CSR，并按立即数掩码清位。
  - CSR 地址位于 `inst[31:20]`。

## M Extension

### MUL rd, rs1, rs2

- 指令形式: `MUL rd, rs1, rs2`
- 所属扩展: `M`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = (x[rs1] * x[rs2])[63:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110011` |

- 详细说明:
  - 返回 XLEN 乘积的低 64 位。

### MULH rd, rs1, rs2

- 指令形式: `MULH rd, rs1, rs2`
- 所属扩展: `M`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = high64(s(x[rs1]) * s(x[rs2]))`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `001` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110011` |

- 详细说明:
  - 返回有符号乘法结果的高 64 位。

### MULHSU rd, rs1, rs2

- 指令形式: `MULHSU rd, rs1, rs2`
- 所属扩展: `M`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = high64(s(x[rs1]) * u(x[rs2]))`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110011` |

- 详细说明:
  - 返回有符号乘无符号乘积的高 64 位。

### MULHU rd, rs1, rs2

- 指令形式: `MULHU rd, rs1, rs2`
- 所属扩展: `M`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = high64(u(x[rs1]) * u(x[rs2]))`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110011` |

- 详细说明:
  - 返回无符号乘法结果的高 64 位。

### DIV rd, rs1, rs2

- 指令形式: `DIV rd, rs1, rs2`
- 所属扩展: `M`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = s(x[rs1]) / s(x[rs2])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `100` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110011` |

- 详细说明:
  - 有符号除法，结果向零舍入。
  - 除零和溢出结果遵循 RISC-V M 扩展定义，不通过异常上报。

### DIVU rd, rs1, rs2

- 指令形式: `DIVU rd, rs1, rs2`
- 所属扩展: `M`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = u(x[rs1]) / u(x[rs2])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `101` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110011` |

- 详细说明:
  - 无符号除法。
  - 除零和溢出结果遵循 RISC-V M 扩展定义，不通过异常上报。

### REM rd, rs1, rs2

- 指令形式: `REM rd, rs1, rs2`
- 所属扩展: `M`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = s(x[rs1]) % s(x[rs2])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `110` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110011` |

- 详细说明:
  - 有符号取余。
  - 除零和溢出结果遵循 RISC-V M 扩展定义，不通过异常上报。

### REMU rd, rs1, rs2

- 指令形式: `REMU rd, rs1, rs2`
- 所属扩展: `M`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = u(x[rs1]) % u(x[rs2])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `111` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0110011` |

- 详细说明:
  - 无符号取余。
  - 除零和溢出结果遵循 RISC-V M 扩展定义，不通过异常上报。

### MULW rd, rs1, rs2

- 指令形式: `MULW rd, rs1, rs2`
- 所属扩展: `M`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = SEXT32(x[rs1][31:0] * x[rs2][31:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0111011` |

- 详细说明:
  - 仅对低 32 位乘法，结果低 32 位再符号扩展。

### DIVW rd, rs1, rs2

- 指令形式: `DIVW rd, rs1, rs2`
- 所属扩展: `M`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = SEXT32(s(x[rs1][31:0]) / s(x[rs2][31:0]))`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `100` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0111011` |

- 详细说明:
  - 仅对低 32 位做有符号除法。

### DIVUW rd, rs1, rs2

- 指令形式: `DIVUW rd, rs1, rs2`
- 所属扩展: `M`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = SEXT32(u(x[rs1][31:0]) / u(x[rs2][31:0]))`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `101` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0111011` |

- 详细说明:
  - 仅对低 32 位做无符号除法。

### REMW rd, rs1, rs2

- 指令形式: `REMW rd, rs1, rs2`
- 所属扩展: `M`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = SEXT32(s(x[rs1][31:0]) % s(x[rs2][31:0]))`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `110` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0111011` |

- 详细说明:
  - 仅对低 32 位做有符号取余。

### REMUW rd, rs1, rs2

- 指令形式: `REMUW rd, rs1, rs2`
- 所属扩展: `M`
- 编码格式: `R-type`
- 类汇编软件表达式: `x[rd] = SEXT32(u(x[rs1][31:0]) % u(x[rs2][31:0]))`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `111` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0111011` |

- 详细说明:
  - 仅对低 32 位做无符号取余。

## A Extension

### LR.W rd, (rs1)

- 指令形式: `LR.W rd, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `x[rd] = sext(M[x[rs1]][31:0]); reserve(x[rs1], 4)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `00010` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `00000` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 读取 32 位字并建立 reservation。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### SC.W rd, rs2, (rs1)

- 指令形式: `SC.W rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `if reservation_ok(x[rs1], 4): M[x[rs1]][31:0] = x[rs2][31:0]; x[rd] = 0 else x[rd] = fail`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `00011` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 条件写 32 位字；成功时 `rd=0`，失败时 `rd!=0`。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### AMOSWAP.W rd, rs2, (rs1)

- 指令形式: `AMOSWAP.W rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `t=M[x[rs1]][31:0]; M[x[rs1]][31:0]=x[rs2][31:0]; x[rd]=sext(t)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `00001` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 原子交换 word。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### AMOADD.W rd, rs2, (rs1)

- 指令形式: `AMOADD.W rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `t=M[x[rs1]][31:0]; M[x[rs1]][31:0]=t + x[rs2][31:0]; x[rd]=sext(t)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `00000` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 原子 fetch-and-add word。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### AMOXOR.W rd, rs2, (rs1)

- 指令形式: `AMOXOR.W rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `t=M[x[rs1]][31:0]; M[x[rs1]][31:0]=t ^ x[rs2][31:0]; x[rd]=sext(t)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `00100` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 原子 fetch-and-xor word。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### AMOAND.W rd, rs2, (rs1)

- 指令形式: `AMOAND.W rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `t=M[x[rs1]][31:0]; M[x[rs1]][31:0]=t & x[rs2][31:0]; x[rd]=sext(t)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `01100` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 原子 fetch-and-and word。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### AMOOR.W rd, rs2, (rs1)

- 指令形式: `AMOOR.W rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `t=M[x[rs1]][31:0]; M[x[rs1]][31:0]=t | x[rs2][31:0]; x[rd]=sext(t)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `01000` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 原子 fetch-and-or word。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### AMOMIN.W rd, rs2, (rs1)

- 指令形式: `AMOMIN.W rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `t=M[x[rs1]][31:0]; M[x[rs1]][31:0]=min_s(t, x[rs2][31:0]); x[rd]=sext(t)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `10000` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 按有符号最小值更新 word。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### AMOMAX.W rd, rs2, (rs1)

- 指令形式: `AMOMAX.W rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `t=M[x[rs1]][31:0]; M[x[rs1]][31:0]=max_s(t, x[rs2][31:0]); x[rd]=sext(t)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `10100` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 按有符号最大值更新 word。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### AMOMINU.W rd, rs2, (rs1)

- 指令形式: `AMOMINU.W rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `t=M[x[rs1]][31:0]; M[x[rs1]][31:0]=min_u(t, x[rs2][31:0]); x[rd]=sext(t)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `11000` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 按无符号最小值更新 word。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### AMOMAXU.W rd, rs2, (rs1)

- 指令形式: `AMOMAXU.W rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `t=M[x[rs1]][31:0]; M[x[rs1]][31:0]=max_u(t, x[rs2][31:0]); x[rd]=sext(t)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `11100` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 按无符号最大值更新 word。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### LR.D rd, (rs1)

- 指令形式: `LR.D rd, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `x[rd] = M[x[rs1]][63:0]; reserve(x[rs1], 8)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `00010` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `00000` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 读取 64 位双字并建立 reservation。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### SC.D rd, rs2, (rs1)

- 指令形式: `SC.D rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `if reservation_ok(x[rs1], 8): M[x[rs1]][63:0] = x[rs2]; x[rd] = 0 else x[rd] = fail`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `00011` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 条件写 64 位双字。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### AMOSWAP.D rd, rs2, (rs1)

- 指令形式: `AMOSWAP.D rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `t=M[x[rs1]][63:0]; M[x[rs1]][63:0]=x[rs2]; x[rd]=t`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `00001` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 原子交换 doubleword。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### AMOADD.D rd, rs2, (rs1)

- 指令形式: `AMOADD.D rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `t=M[x[rs1]][63:0]; M[x[rs1]][63:0]=t + x[rs2]; x[rd]=t`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `00000` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 原子 fetch-and-add doubleword。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### AMOXOR.D rd, rs2, (rs1)

- 指令形式: `AMOXOR.D rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `t=M[x[rs1]][63:0]; M[x[rs1]][63:0]=t ^ x[rs2]; x[rd]=t`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `00100` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 原子 fetch-and-xor doubleword。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### AMOAND.D rd, rs2, (rs1)

- 指令形式: `AMOAND.D rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `t=M[x[rs1]][63:0]; M[x[rs1]][63:0]=t & x[rs2]; x[rd]=t`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `01100` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 原子 fetch-and-and doubleword。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### AMOOR.D rd, rs2, (rs1)

- 指令形式: `AMOOR.D rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `t=M[x[rs1]][63:0]; M[x[rs1]][63:0]=t | x[rs2]; x[rd]=t`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `01000` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 原子 fetch-and-or doubleword。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### AMOMIN.D rd, rs2, (rs1)

- 指令形式: `AMOMIN.D rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `t=M[x[rs1]][63:0]; M[x[rs1]][63:0]=min_s(t, x[rs2]); x[rd]=t`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `10000` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 按有符号最小值更新 doubleword。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### AMOMAX.D rd, rs2, (rs1)

- 指令形式: `AMOMAX.D rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `t=M[x[rs1]][63:0]; M[x[rs1]][63:0]=max_s(t, x[rs2]); x[rd]=t`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `10100` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 按有符号最大值更新 doubleword。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### AMOMINU.D rd, rs2, (rs1)

- 指令形式: `AMOMINU.D rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `t=M[x[rs1]][63:0]; M[x[rs1]][63:0]=min_u(t, x[rs2]); x[rd]=t`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `11000` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 按无符号最小值更新 doubleword。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

### AMOMAXU.D rd, rs2, (rs1)

- 指令形式: `AMOMAXU.D rd, rs2, (rs1)`
- 所属扩展: `A`
- 编码格式: `R-type (atomic)`
- 类汇编软件表达式: `t=M[x[rs1]][63:0]; M[x[rs1]][63:0]=max_u(t, x[rs2]); x[rd]=t`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `11100` |
| `inst[26]` | `aq` |
| `inst[25]` | `rl` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0101111` |

- 详细说明:
  - 按无符号最大值更新 doubleword。
  - `.aq` 与 `.rl` 位分别提供 acquire / release 顺序语义。

## F Extension

### FLW rd, imm12(rs1)

- 指令形式: `FLW rd, imm12(rs1)`
- 所属扩展: `F`
- 编码格式: `I-type`
- 类汇编软件表达式: `f[rd] = M[x[rs1] + sext(imm12)][31:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `imm[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0000111` |

- 详细说明:
  - 读取单精度浮点位模式到浮点寄存器。
  - 不会改写 NaN payload。

### FSW rs2, imm12(rs1)

- 指令形式: `FSW rs2, imm12(rs1)`
- 所属扩展: `F`
- 编码格式: `S-type`
- 类汇编软件表达式: `M[x[rs1] + sext(imm12)][31:0] = f[rs2][31:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `imm[11:5]` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `imm[4:0]` |
| `inst[6:0]` | `0100111` |

- 详细说明:
  - 把单精度浮点位模式写入内存。

### FMADD.S rd, rs1, rs2, rs3, rm

- 指令形式: `FMADD.S rd, rs1, rs2, rs3, rm`
- 所属扩展: `F`
- 编码格式: `R4-type`
- 类汇编软件表达式: `f[rd] = (f[rs1] * f[rs2]) + f[rs3]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `rs3` |
| `inst[26:25]` | `fmt=00` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1000011` |

- 详细说明:
  - 单精度 fused multiply-add，只做一次最终舍入。

### FMSUB.S rd, rs1, rs2, rs3, rm

- 指令形式: `FMSUB.S rd, rs1, rs2, rs3, rm`
- 所属扩展: `F`
- 编码格式: `R4-type`
- 类汇编软件表达式: `f[rd] = (f[rs1] * f[rs2]) - f[rs3]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `rs3` |
| `inst[26:25]` | `fmt=00` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1000111` |

- 详细说明:
  - 单精度 fused multiply-sub。

### FNMSUB.S rd, rs1, rs2, rs3, rm

- 指令形式: `FNMSUB.S rd, rs1, rs2, rs3, rm`
- 所属扩展: `F`
- 编码格式: `R4-type`
- 类汇编软件表达式: `f[rd] = -(f[rs1] * f[rs2]) + f[rs3]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `rs3` |
| `inst[26:25]` | `fmt=00` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1001011` |

- 详细说明:
  - 对乘积取负后再加。

### FNMADD.S rd, rs1, rs2, rs3, rm

- 指令形式: `FNMADD.S rd, rs1, rs2, rs3, rm`
- 所属扩展: `F`
- 编码格式: `R4-type`
- 类汇编软件表达式: `f[rd] = -(f[rs1] * f[rs2]) - f[rs3]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `rs3` |
| `inst[26:25]` | `fmt=00` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1001111` |

- 详细说明:
  - 对乘积取负后再减。

### FADD.S rd, rs1, rs2, rm

- 指令形式: `FADD.S rd, rs1, rs2, rm`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = f[rs1] + f[rs2]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 单精度加法。

### FSUB.S rd, rs1, rs2, rm

- 指令形式: `FSUB.S rd, rs1, rs2, rm`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = f[rs1] - f[rs2]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000100` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 单精度减法。

### FMUL.S rd, rs1, rs2, rm

- 指令形式: `FMUL.S rd, rs1, rs2, rm`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = f[rs1] * f[rs2]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0001000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 单精度乘法。

### FDIV.S rd, rs1, rs2, rm

- 指令形式: `FDIV.S rd, rs1, rs2, rm`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = f[rs1] / f[rs2]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0001100` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 单精度除法。

### FSQRT.S rd, rs1, rm

- 指令形式: `FSQRT.S rd, rs1, rm`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = sqrt(f[rs1])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0101100` |
| `inst[24:20]` | `00000` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 单精度平方根。

### FSGNJ.S rd, rs1, rs2

- 指令形式: `FSGNJ.S rd, rs1, rs2`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = sign_from(rs2, magnitude_from=rs1)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0010000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 结果绝对值取自 `rs1`，符号位取自 `rs2`。

### FSGNJN.S rd, rs1, rs2

- 指令形式: `FSGNJN.S rd, rs1, rs2`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = sign_from(~rs2.sign, magnitude_from=rs1)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0010000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `001` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 符号位取 `rs2` 符号的反。

### FSGNJX.S rd, rs1, rs2

- 指令形式: `FSGNJX.S rd, rs1, rs2`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = sign_from(rs1.sign ^ rs2.sign, magnitude_from=rs1)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0010000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 符号位取两源符号异或。

### FMIN.S rd, rs1, rs2

- 指令形式: `FMIN.S rd, rs1, rs2`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = minNum(f[rs1], f[rs2])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0010100` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 返回较小单精度值，按规范处理 NaN。

### FMAX.S rd, rs1, rs2

- 指令形式: `FMAX.S rd, rs1, rs2`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = maxNum(f[rs1], f[rs2])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0010100` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `001` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 返回较大单精度值，按规范处理 NaN。

### FEQ.S rd, rs1, rs2

- 指令形式: `FEQ.S rd, rs1, rs2`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `x[rd] = 1 if f[rs1] == f[rs2] else 0`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1010000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 安静比较，相等时写 1。

### FLT.S rd, rs1, rs2

- 指令形式: `FLT.S rd, rs1, rs2`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `x[rd] = 1 if f[rs1] < f[rs2] else 0`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1010000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `001` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 有序小于比较。

### FLE.S rd, rs1, rs2

- 指令形式: `FLE.S rd, rs1, rs2`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `x[rd] = 1 if f[rs1] <= f[rs2] else 0`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1010000` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 有序小于等于比较。

### FCLASS.S rd, rs1

- 指令形式: `FCLASS.S rd, rs1`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `x[rd] = fpclass_mask(f[rs1])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1110000` |
| `inst[24:20]` | `00000` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `001` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 输出 10-bit 分类掩码。

### FMV.X.W rd, rs1

- 指令形式: `FMV.X.W rd, rs1`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `x[rd] = bitcast_from_f32(f[rs1])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1110000` |
| `inst[24:20]` | `00000` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 把单精度位模式搬到整数寄存器。

### FMV.W.X rd, rs1

- 指令形式: `FMV.W.X rd, rs1`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = bitcast_from_x32(x[rs1])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1111000` |
| `inst[24:20]` | `00000` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 把整数寄存器低 32 位位模式搬到浮点寄存器。

### FCVT.W.S rd, rs1, rm

- 指令形式: `FCVT.W.S rd, rs1, rm`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP convert`
- 类汇编软件表达式: `x[rd] = fp_to_i32(f[rs1], rm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1100000` |
| `inst[24:20]` | `00000` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 单精度转有符号 32 位整数。

### FCVT.WU.S rd, rs1, rm

- 指令形式: `FCVT.WU.S rd, rs1, rm`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP convert`
- 类汇编软件表达式: `x[rd] = fp_to_u32(f[rs1], rm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1100000` |
| `inst[24:20]` | `00001` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 单精度转无符号 32 位整数。

### FCVT.L.S rd, rs1, rm

- 指令形式: `FCVT.L.S rd, rs1, rm`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP convert`
- 类汇编软件表达式: `x[rd] = fp_to_i64(f[rs1], rm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1100000` |
| `inst[24:20]` | `00010` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 单精度转有符号 64 位整数。

### FCVT.LU.S rd, rs1, rm

- 指令形式: `FCVT.LU.S rd, rs1, rm`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP convert`
- 类汇编软件表达式: `x[rd] = fp_to_u64(f[rs1], rm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1100000` |
| `inst[24:20]` | `00011` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 单精度转无符号 64 位整数。

### FCVT.S.W rd, rs1, rm

- 指令形式: `FCVT.S.W rd, rs1, rm`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP convert`
- 类汇编软件表达式: `f[rd] = i32_to_f32(x[rs1], rm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1101000` |
| `inst[24:20]` | `00000` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 有符号 32 位整数转单精度浮点。

### FCVT.S.WU rd, rs1, rm

- 指令形式: `FCVT.S.WU rd, rs1, rm`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP convert`
- 类汇编软件表达式: `f[rd] = u32_to_f32(x[rs1], rm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1101000` |
| `inst[24:20]` | `00001` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 无符号 32 位整数转单精度浮点。

### FCVT.S.L rd, rs1, rm

- 指令形式: `FCVT.S.L rd, rs1, rm`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP convert`
- 类汇编软件表达式: `f[rd] = i64_to_f32(x[rs1], rm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1101000` |
| `inst[24:20]` | `00010` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 有符号 64 位整数转单精度浮点。

### FCVT.S.LU rd, rs1, rm

- 指令形式: `FCVT.S.LU rd, rs1, rm`
- 所属扩展: `F`
- 编码格式: `R-type / OP-FP convert`
- 类汇编软件表达式: `f[rd] = u64_to_f32(x[rs1], rm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1101000` |
| `inst[24:20]` | `00011` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 无符号 64 位整数转单精度浮点。

## D Extension

### FLD rd, imm12(rs1)

- 指令形式: `FLD rd, imm12(rs1)`
- 所属扩展: `D`
- 编码格式: `I-type`
- 类汇编软件表达式: `f[rd] = M[x[rs1] + sext(imm12)][63:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:20]` | `imm[11:0]` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `0000111` |

- 详细说明:
  - 读取双精度浮点位模式到浮点寄存器。

### FSD rs2, imm12(rs1)

- 指令形式: `FSD rs2, imm12(rs1)`
- 所属扩展: `D`
- 编码格式: `S-type`
- 类汇编软件表达式: `M[x[rs1] + sext(imm12)][63:0] = f[rs2][63:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `imm[11:5]` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `011` |
| `inst[11:7]` | `imm[4:0]` |
| `inst[6:0]` | `0100111` |

- 详细说明:
  - 把双精度浮点位模式写入内存。

### FMADD.D rd, rs1, rs2, rs3, rm

- 指令形式: `FMADD.D rd, rs1, rs2, rs3, rm`
- 所属扩展: `D`
- 编码格式: `R4-type`
- 类汇编软件表达式: `f[rd] = (f[rs1] * f[rs2]) + f[rs3]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `rs3` |
| `inst[26:25]` | `fmt=01` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1000011` |

- 详细说明:
  - 双精度 fused multiply-add。

### FMSUB.D rd, rs1, rs2, rs3, rm

- 指令形式: `FMSUB.D rd, rs1, rs2, rs3, rm`
- 所属扩展: `D`
- 编码格式: `R4-type`
- 类汇编软件表达式: `f[rd] = (f[rs1] * f[rs2]) - f[rs3]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `rs3` |
| `inst[26:25]` | `fmt=01` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1000111` |

- 详细说明:
  - 双精度 fused multiply-sub。

### FNMSUB.D rd, rs1, rs2, rs3, rm

- 指令形式: `FNMSUB.D rd, rs1, rs2, rs3, rm`
- 所属扩展: `D`
- 编码格式: `R4-type`
- 类汇编软件表达式: `f[rd] = -(f[rs1] * f[rs2]) + f[rs3]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `rs3` |
| `inst[26:25]` | `fmt=01` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1001011` |

- 详细说明:
  - 对双精度乘积取负后再加。

### FNMADD.D rd, rs1, rs2, rs3, rm

- 指令形式: `FNMADD.D rd, rs1, rs2, rs3, rm`
- 所属扩展: `D`
- 编码格式: `R4-type`
- 类汇编软件表达式: `f[rd] = -(f[rs1] * f[rs2]) - f[rs3]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:27]` | `rs3` |
| `inst[26:25]` | `fmt=01` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1001111` |

- 详细说明:
  - 对双精度乘积取负后再减。

### FADD.D rd, rs1, rs2, rm

- 指令形式: `FADD.D rd, rs1, rs2, rm`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = f[rs1] + f[rs2]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 双精度加法。

### FSUB.D rd, rs1, rs2, rm

- 指令形式: `FSUB.D rd, rs1, rs2, rm`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = f[rs1] - f[rs2]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0000101` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 双精度减法。

### FMUL.D rd, rs1, rs2, rm

- 指令形式: `FMUL.D rd, rs1, rs2, rm`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = f[rs1] * f[rs2]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0001001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 双精度乘法。

### FDIV.D rd, rs1, rs2, rm

- 指令形式: `FDIV.D rd, rs1, rs2, rm`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = f[rs1] / f[rs2]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0001101` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 双精度除法。

### FSQRT.D rd, rs1, rm

- 指令形式: `FSQRT.D rd, rs1, rm`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = sqrt(f[rs1])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0101101` |
| `inst[24:20]` | `00000` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 双精度平方根。

### FSGNJ.D rd, rs1, rs2

- 指令形式: `FSGNJ.D rd, rs1, rs2`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = sign_from(rs2, magnitude_from=rs1)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0010001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 结果绝对值取自 `rs1`，符号位取自 `rs2`。

### FSGNJN.D rd, rs1, rs2

- 指令形式: `FSGNJN.D rd, rs1, rs2`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = sign_from(~rs2.sign, magnitude_from=rs1)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0010001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `001` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 符号位取 `rs2` 符号的反。

### FSGNJX.D rd, rs1, rs2

- 指令形式: `FSGNJX.D rd, rs1, rs2`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = sign_from(rs1.sign ^ rs2.sign, magnitude_from=rs1)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0010001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 符号位取两源符号异或。

### FMIN.D rd, rs1, rs2

- 指令形式: `FMIN.D rd, rs1, rs2`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = minNum(f[rs1], f[rs2])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0010101` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 返回较小双精度值。

### FMAX.D rd, rs1, rs2

- 指令形式: `FMAX.D rd, rs1, rs2`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = maxNum(f[rs1], f[rs2])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0010101` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `001` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 返回较大双精度值。

### FEQ.D rd, rs1, rs2

- 指令形式: `FEQ.D rd, rs1, rs2`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `x[rd] = 1 if f[rs1] == f[rs2] else 0`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1010001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `010` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 双精度相等比较。

### FLT.D rd, rs1, rs2

- 指令形式: `FLT.D rd, rs1, rs2`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `x[rd] = 1 if f[rs1] < f[rs2] else 0`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1010001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `001` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 双精度小于比较。

### FLE.D rd, rs1, rs2

- 指令形式: `FLE.D rd, rs1, rs2`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `x[rd] = 1 if f[rs1] <= f[rs2] else 0`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1010001` |
| `inst[24:20]` | `rs2` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 双精度小于等于比较。

### FCLASS.D rd, rs1

- 指令形式: `FCLASS.D rd, rs1`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `x[rd] = fpclass_mask(f[rs1])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1110001` |
| `inst[24:20]` | `00000` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `001` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 输出双精度分类掩码。

### FMV.X.D rd, rs1

- 指令形式: `FMV.X.D rd, rs1`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `x[rd] = bitcast_from_f64(f[rs1])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1110001` |
| `inst[24:20]` | `00000` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 把双精度位模式搬到整数寄存器。

### FMV.D.X rd, rs1

- 指令形式: `FMV.D.X rd, rs1`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP`
- 类汇编软件表达式: `f[rd] = bitcast_from_x64(x[rs1])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1111001` |
| `inst[24:20]` | `00000` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `000` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 把整数寄存器 64 位位模式搬到浮点寄存器。

### FCVT.W.D rd, rs1, rm

- 指令形式: `FCVT.W.D rd, rs1, rm`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP convert`
- 类汇编软件表达式: `x[rd] = fp_to_i32(f[rs1], rm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1100001` |
| `inst[24:20]` | `00000` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 双精度转有符号 32 位整数。

### FCVT.WU.D rd, rs1, rm

- 指令形式: `FCVT.WU.D rd, rs1, rm`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP convert`
- 类汇编软件表达式: `x[rd] = fp_to_u32(f[rs1], rm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1100001` |
| `inst[24:20]` | `00001` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 双精度转无符号 32 位整数。

### FCVT.L.D rd, rs1, rm

- 指令形式: `FCVT.L.D rd, rs1, rm`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP convert`
- 类汇编软件表达式: `x[rd] = fp_to_i64(f[rs1], rm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1100001` |
| `inst[24:20]` | `00010` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 双精度转有符号 64 位整数。

### FCVT.LU.D rd, rs1, rm

- 指令形式: `FCVT.LU.D rd, rs1, rm`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP convert`
- 类汇编软件表达式: `x[rd] = fp_to_u64(f[rs1], rm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1100001` |
| `inst[24:20]` | `00011` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 双精度转无符号 64 位整数。

### FCVT.D.W rd, rs1, rm

- 指令形式: `FCVT.D.W rd, rs1, rm`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP convert`
- 类汇编软件表达式: `f[rd] = i32_to_f64(x[rs1], rm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1101001` |
| `inst[24:20]` | `00000` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 有符号 32 位整数转双精度浮点。

### FCVT.D.WU rd, rs1, rm

- 指令形式: `FCVT.D.WU rd, rs1, rm`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP convert`
- 类汇编软件表达式: `f[rd] = u32_to_f64(x[rs1], rm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1101001` |
| `inst[24:20]` | `00001` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 无符号 32 位整数转双精度浮点。

### FCVT.D.L rd, rs1, rm

- 指令形式: `FCVT.D.L rd, rs1, rm`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP convert`
- 类汇编软件表达式: `f[rd] = i64_to_f64(x[rs1], rm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1101001` |
| `inst[24:20]` | `00010` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 有符号 64 位整数转双精度浮点。

### FCVT.D.LU rd, rs1, rm

- 指令形式: `FCVT.D.LU rd, rs1, rm`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP convert`
- 类汇编软件表达式: `f[rd] = u64_to_f64(x[rs1], rm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `1101001` |
| `inst[24:20]` | `00011` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 无符号 64 位整数转双精度浮点。

### FCVT.S.D rd, rs1, rm

- 指令形式: `FCVT.S.D rd, rs1, rm`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP convert`
- 类汇编软件表达式: `f[rd] = f64_to_f32(f[rs1], rm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0100000` |
| `inst[24:20]` | `00001` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 双精度转单精度。

### FCVT.D.S rd, rs1, rm

- 指令形式: `FCVT.D.S rd, rs1, rm`
- 所属扩展: `D`
- 编码格式: `R-type / OP-FP convert`
- 类汇编软件表达式: `f[rd] = f32_to_f64(f[rs1])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[31:25]` | `0100001` |
| `inst[24:20]` | `00000` |
| `inst[19:15]` | `rs1` |
| `inst[14:12]` | `rm` |
| `inst[11:7]` | `rd` |
| `inst[6:0]` | `1010011` |

- 详细说明:
  - 单精度转双精度。

## C Extension (RV64GC-valid compressed instructions)

### C.ADDI4SPN rd', nzuimm

- 指令形式: `C.ADDI4SPN rd', nzuimm`
- 所属扩展: `C`
- 编码格式: `CIW`
- 类汇编软件表达式: `x[rd'] = x[2] + zext(nzuimm << 2)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `000` |
| `inst[12:5]` | `nzuimm[5:4|9:6|2|3]` |
| `inst[4:2]` | `rd'` |
| `inst[1:0]` | `00` |

- 详细说明:
  - 把栈指针 `sp` 加上非零零扩展偏移，结果写入压缩寄存器。
  - 常用于生成栈上对象地址。

### C.FLD rd', offset(rs1')

- 指令形式: `C.FLD rd', offset(rs1')`
- 所属扩展: `C`
- 编码格式: `CL`
- 类汇编软件表达式: `f[rd'] = M[x[rs1'] + zext(offset)][63:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `001` |
| `inst[12:10]` | `offset[5:3]` |
| `inst[9:7]` | `rs1'` |
| `inst[6:5]` | `offset[7:6]` |
| `inst[4:2]` | `rd'` |
| `inst[1:0]` | `00` |

- 详细说明:
  - 压缩双精度 load。

### C.LW rd', offset(rs1')

- 指令形式: `C.LW rd', offset(rs1')`
- 所属扩展: `C`
- 编码格式: `CL`
- 类汇编软件表达式: `x[rd'] = sext(M[x[rs1'] + zext(offset)][31:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `010` |
| `inst[12:10]` | `offset[5:3]` |
| `inst[9:7]` | `rs1'` |
| `inst[6]` | `offset[2]` |
| `inst[5]` | `offset[6]` |
| `inst[4:2]` | `rd'` |
| `inst[1:0]` | `00` |

- 详细说明:
  - 压缩 word load。

### C.LD rd', offset(rs1')

- 指令形式: `C.LD rd', offset(rs1')`
- 所属扩展: `C`
- 编码格式: `CL`
- 类汇编软件表达式: `x[rd'] = M[x[rs1'] + zext(offset)][63:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `011` |
| `inst[12:10]` | `offset[5:3]` |
| `inst[9:7]` | `rs1'` |
| `inst[6:5]` | `offset[7:6]` |
| `inst[4:2]` | `rd'` |
| `inst[1:0]` | `00` |

- 详细说明:
  - RV64C 压缩 doubleword load。

### C.FSD rs2', offset(rs1')

- 指令形式: `C.FSD rs2', offset(rs1')`
- 所属扩展: `C`
- 编码格式: `CS`
- 类汇编软件表达式: `M[x[rs1'] + zext(offset)][63:0] = f[rs2'][63:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `101` |
| `inst[12:10]` | `offset[5:3]` |
| `inst[9:7]` | `rs1'` |
| `inst[6:5]` | `offset[7:6]` |
| `inst[4:2]` | `rs2'` |
| `inst[1:0]` | `00` |

- 详细说明:
  - 压缩双精度 store。

### C.SW rs2', offset(rs1')

- 指令形式: `C.SW rs2', offset(rs1')`
- 所属扩展: `C`
- 编码格式: `CS`
- 类汇编软件表达式: `M[x[rs1'] + zext(offset)][31:0] = x[rs2'][31:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `110` |
| `inst[12:10]` | `offset[5:3]` |
| `inst[9:7]` | `rs1'` |
| `inst[6]` | `offset[2]` |
| `inst[5]` | `offset[6]` |
| `inst[4:2]` | `rs2'` |
| `inst[1:0]` | `00` |

- 详细说明:
  - 压缩 word store。

### C.SD rs2', offset(rs1')

- 指令形式: `C.SD rs2', offset(rs1')`
- 所属扩展: `C`
- 编码格式: `CS`
- 类汇编软件表达式: `M[x[rs1'] + zext(offset)][63:0] = x[rs2']`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `111` |
| `inst[12:10]` | `offset[5:3]` |
| `inst[9:7]` | `rs1'` |
| `inst[6:5]` | `offset[7:6]` |
| `inst[4:2]` | `rs2'` |
| `inst[1:0]` | `00` |

- 详细说明:
  - RV64C 压缩 doubleword store。

### C.NOP

- 指令形式: `C.NOP`
- 所属扩展: `C`
- 编码格式: `CI`
- 类汇编软件表达式: `pc = pc + 2`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `000` |
| `inst[12]` | `imm[5]=0` |
| `inst[11:7]` | `rd/rs1=x0` |
| `inst[6:2]` | `imm[4:0]=00000` |
| `inst[1:0]` | `01` |

- 详细说明:
  - 压缩 no-op。

### C.ADDI rd, imm

- 指令形式: `C.ADDI rd, imm`
- 所属扩展: `C`
- 编码格式: `CI`
- 类汇编软件表达式: `x[rd] = x[rd] + sext(imm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `000` |
| `inst[12]` | `imm[5]` |
| `inst[11:7]` | `rd/rs1` |
| `inst[6:2]` | `imm[4:0]` |
| `inst[1:0]` | `01` |

- 详细说明:
  - 压缩 addi，`rd` 同时作为源和目的。

### C.ADDIW rd, imm

- 指令形式: `C.ADDIW rd, imm`
- 所属扩展: `C`
- 编码格式: `CI`
- 类汇编软件表达式: `x[rd] = SEXT32(x[rd][31:0] + sext(imm))`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `001` |
| `inst[12]` | `imm[5]` |
| `inst[11:7]` | `rd/rs1` |
| `inst[6:2]` | `imm[4:0]` |
| `inst[1:0]` | `01` |

- 详细说明:
  - RV64C 压缩 addiw。

### C.LI rd, imm

- 指令形式: `C.LI rd, imm`
- 所属扩展: `C`
- 编码格式: `CI`
- 类汇编软件表达式: `x[rd] = sext(imm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `010` |
| `inst[12]` | `imm[5]` |
| `inst[11:7]` | `rd` |
| `inst[6:2]` | `imm[4:0]` |
| `inst[1:0]` | `01` |

- 详细说明:
  - 压缩装载小立即数。

### C.ADDI16SP nzimm

- 指令形式: `C.ADDI16SP nzimm`
- 所属扩展: `C`
- 编码格式: `CI`
- 类汇编软件表达式: `x[2] = x[2] + sext(nzimm << 4)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `011` |
| `inst[12]` | `nzimm[9]` |
| `inst[11:7]` | `x2` |
| `inst[6:2]` | `nzimm[4|6|8:7|5]` |
| `inst[1:0]` | `01` |

- 详细说明:
  - 仅针对 `sp` 的压缩栈调整指令。

### C.LUI rd, nzimm

- 指令形式: `C.LUI rd, nzimm`
- 所属扩展: `C`
- 编码格式: `CI`
- 类汇编软件表达式: `x[rd] = sext(nzimm << 12)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `011` |
| `inst[12]` | `imm[17]` |
| `inst[11:7]` | `rd` |
| `inst[6:2]` | `imm[16:12]` |
| `inst[1:0]` | `01` |

- 详细说明:
  - 压缩高位立即数装载。

### C.SRLI rd', shamt

- 指令形式: `C.SRLI rd', shamt`
- 所属扩展: `C`
- 编码格式: `CB`
- 类汇编软件表达式: `x[rd'] = u(x[rd']) >> shamt`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `100` |
| `inst[12]` | `shamt[5]` |
| `inst[11:10]` | `00` |
| `inst[9:7]` | `rd'` |
| `inst[6:2]` | `shamt[4:0]` |
| `inst[1:0]` | `01` |

- 详细说明:
  - 压缩逻辑右移立即数。

### C.SRAI rd', shamt

- 指令形式: `C.SRAI rd', shamt`
- 所属扩展: `C`
- 编码格式: `CB`
- 类汇编软件表达式: `x[rd'] = s(x[rd']) >> shamt`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `100` |
| `inst[12]` | `shamt[5]` |
| `inst[11:10]` | `01` |
| `inst[9:7]` | `rd'` |
| `inst[6:2]` | `shamt[4:0]` |
| `inst[1:0]` | `01` |

- 详细说明:
  - 压缩算术右移立即数。

### C.ANDI rd', imm

- 指令形式: `C.ANDI rd', imm`
- 所属扩展: `C`
- 编码格式: `CB`
- 类汇编软件表达式: `x[rd'] = x[rd'] & sext(imm)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `100` |
| `inst[12]` | `imm[5]` |
| `inst[11:10]` | `10` |
| `inst[9:7]` | `rd'` |
| `inst[6:2]` | `imm[4:0]` |
| `inst[1:0]` | `01` |

- 详细说明:
  - 压缩按位与立即数。

### C.SUB rd', rs2'

- 指令形式: `C.SUB rd', rs2'`
- 所属扩展: `C`
- 编码格式: `CA`
- 类汇编软件表达式: `x[rd'] = x[rd'] - x[rs2']`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `100` |
| `inst[12]` | `0` |
| `inst[11:10]` | `11` |
| `inst[9:7]` | `rd'` |
| `inst[6:5]` | `00` |
| `inst[4:2]` | `rs2'` |
| `inst[1:0]` | `01` |

- 详细说明:
  - 压缩减法。

### C.XOR rd', rs2'

- 指令形式: `C.XOR rd', rs2'`
- 所属扩展: `C`
- 编码格式: `CA`
- 类汇编软件表达式: `x[rd'] = x[rd'] ^ x[rs2']`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `100` |
| `inst[12]` | `0` |
| `inst[11:10]` | `11` |
| `inst[9:7]` | `rd'` |
| `inst[6:5]` | `01` |
| `inst[4:2]` | `rs2'` |
| `inst[1:0]` | `01` |

- 详细说明:
  - 压缩异或。

### C.OR rd', rs2'

- 指令形式: `C.OR rd', rs2'`
- 所属扩展: `C`
- 编码格式: `CA`
- 类汇编软件表达式: `x[rd'] = x[rd'] | x[rs2']`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `100` |
| `inst[12]` | `0` |
| `inst[11:10]` | `11` |
| `inst[9:7]` | `rd'` |
| `inst[6:5]` | `10` |
| `inst[4:2]` | `rs2'` |
| `inst[1:0]` | `01` |

- 详细说明:
  - 压缩按位或。

### C.AND rd', rs2'

- 指令形式: `C.AND rd', rs2'`
- 所属扩展: `C`
- 编码格式: `CA`
- 类汇编软件表达式: `x[rd'] = x[rd'] & x[rs2']`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `100` |
| `inst[12]` | `0` |
| `inst[11:10]` | `11` |
| `inst[9:7]` | `rd'` |
| `inst[6:5]` | `11` |
| `inst[4:2]` | `rs2'` |
| `inst[1:0]` | `01` |

- 详细说明:
  - 压缩按位与。

### C.SUBW rd', rs2'

- 指令形式: `C.SUBW rd', rs2'`
- 所属扩展: `C`
- 编码格式: `CA`
- 类汇编软件表达式: `x[rd'] = SEXT32(x[rd'][31:0] - x[rs2'][31:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `100` |
| `inst[12]` | `1` |
| `inst[11:10]` | `11` |
| `inst[9:7]` | `rd'` |
| `inst[6:5]` | `00` |
| `inst[4:2]` | `rs2'` |
| `inst[1:0]` | `01` |

- 详细说明:
  - RV64C 压缩减法并做 32 位符号扩展。

### C.ADDW rd', rs2'

- 指令形式: `C.ADDW rd', rs2'`
- 所属扩展: `C`
- 编码格式: `CA`
- 类汇编软件表达式: `x[rd'] = SEXT32(x[rd'][31:0] + x[rs2'][31:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `100` |
| `inst[12]` | `1` |
| `inst[11:10]` | `11` |
| `inst[9:7]` | `rd'` |
| `inst[6:5]` | `01` |
| `inst[4:2]` | `rs2'` |
| `inst[1:0]` | `01` |

- 详细说明:
  - RV64C 压缩加法并做 32 位符号扩展。

### C.J offset

- 指令形式: `C.J offset`
- 所属扩展: `C`
- 编码格式: `CJ`
- 类汇编软件表达式: `pc = pc + sext(imm << 1)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `101` |
| `inst[12:2]` | `imm[11|4|9:8|10|6|7|3:1|5]` |
| `inst[1:0]` | `01` |

- 详细说明:
  - 压缩无条件跳转。

### C.BEQZ rs1', offset

- 指令形式: `C.BEQZ rs1', offset`
- 所属扩展: `C`
- 编码格式: `CB`
- 类汇编软件表达式: `if x[rs1'] == 0: pc = pc + sext(imm << 1)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `110` |
| `inst[12:10]` | `imm[8|4:3]` |
| `inst[9:7]` | `rs1'` |
| `inst[6:2]` | `imm[7:6|2:1|5]` |
| `inst[1:0]` | `01` |

- 详细说明:
  - 压缩分支: 等于零时跳转。

### C.BNEZ rs1', offset

- 指令形式: `C.BNEZ rs1', offset`
- 所属扩展: `C`
- 编码格式: `CB`
- 类汇编软件表达式: `if x[rs1'] != 0: pc = pc + sext(imm << 1)`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `111` |
| `inst[12:10]` | `imm[8|4:3]` |
| `inst[9:7]` | `rs1'` |
| `inst[6:2]` | `imm[7:6|2:1|5]` |
| `inst[1:0]` | `01` |

- 详细说明:
  - 压缩分支: 非零时跳转。

### C.SLLI rd, shamt

- 指令形式: `C.SLLI rd, shamt`
- 所属扩展: `C`
- 编码格式: `CI`
- 类汇编软件表达式: `x[rd] = x[rd] << shamt`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `000` |
| `inst[12]` | `shamt[5]` |
| `inst[11:7]` | `rd/rs1` |
| `inst[6:2]` | `shamt[4:0]` |
| `inst[1:0]` | `10` |

- 详细说明:
  - 压缩逻辑左移立即数。

### C.FLDSP rd, offset(sp)

- 指令形式: `C.FLDSP rd, offset(sp)`
- 所属扩展: `C`
- 编码格式: `CI`
- 类汇编软件表达式: `f[rd] = M[x[2] + zext(offset)][63:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `001` |
| `inst[12]` | `offset[5]` |
| `inst[11:7]` | `rd` |
| `inst[6:2]` | `offset[4:3|8:6]` |
| `inst[1:0]` | `10` |

- 详细说明:
  - 以 `sp` 为基址的压缩双精度 load。

### C.LWSP rd, offset(sp)

- 指令形式: `C.LWSP rd, offset(sp)`
- 所属扩展: `C`
- 编码格式: `CI`
- 类汇编软件表达式: `x[rd] = sext(M[x[2] + zext(offset)][31:0])`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `010` |
| `inst[12]` | `offset[5]` |
| `inst[11:7]` | `rd` |
| `inst[6:2]` | `offset[4:2|7:6]` |
| `inst[1:0]` | `10` |

- 详细说明:
  - 以 `sp` 为基址的压缩 word load。

### C.LDSP rd, offset(sp)

- 指令形式: `C.LDSP rd, offset(sp)`
- 所属扩展: `C`
- 编码格式: `CI`
- 类汇编软件表达式: `x[rd] = M[x[2] + zext(offset)][63:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `011` |
| `inst[12]` | `offset[5]` |
| `inst[11:7]` | `rd` |
| `inst[6:2]` | `offset[4:3|8:6]` |
| `inst[1:0]` | `10` |

- 详细说明:
  - RV64C 以 `sp` 为基址的压缩 doubleword load。

### C.JR rs1

- 指令形式: `C.JR rs1`
- 所属扩展: `C`
- 编码格式: `CR`
- 类汇编软件表达式: `pc = x[rs1]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `100` |
| `inst[12]` | `0` |
| `inst[11:7]` | `rs1 != x0` |
| `inst[6:2]` | `00000` |
| `inst[1:0]` | `10` |

- 详细说明:
  - 压缩寄存器间接跳转。

### C.MV rd, rs2

- 指令形式: `C.MV rd, rs2`
- 所属扩展: `C`
- 编码格式: `CR`
- 类汇编软件表达式: `x[rd] = x[rs2]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `100` |
| `inst[12]` | `0` |
| `inst[11:7]` | `rd` |
| `inst[6:2]` | `rs2 != x0` |
| `inst[1:0]` | `10` |

- 详细说明:
  - 压缩寄存器搬运。

### C.EBREAK

- 指令形式: `C.EBREAK`
- 所属扩展: `C`
- 编码格式: `CR`
- 类汇编软件表达式: `raise Breakpoint`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `100` |
| `inst[12]` | `1` |
| `inst[11:7]` | `00000` |
| `inst[6:2]` | `00000` |
| `inst[1:0]` | `10` |

- 详细说明:
  - 压缩断点异常。

### C.JALR rs1

- 指令形式: `C.JALR rs1`
- 所属扩展: `C`
- 编码格式: `CR`
- 类汇编软件表达式: `tmp = pc + 2; pc = x[rs1]; x[1] = tmp`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `100` |
| `inst[12]` | `1` |
| `inst[11:7]` | `rs1 != x0` |
| `inst[6:2]` | `00000` |
| `inst[1:0]` | `10` |

- 详细说明:
  - 压缩寄存器间接调用，写回 `ra=x1`。

### C.ADD rd, rs2

- 指令形式: `C.ADD rd, rs2`
- 所属扩展: `C`
- 编码格式: `CR`
- 类汇编软件表达式: `x[rd] = x[rd] + x[rs2]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `100` |
| `inst[12]` | `1` |
| `inst[11:7]` | `rd` |
| `inst[6:2]` | `rs2 != x0` |
| `inst[1:0]` | `10` |

- 详细说明:
  - 压缩加法。

### C.FSDSP rs2, offset(sp)

- 指令形式: `C.FSDSP rs2, offset(sp)`
- 所属扩展: `C`
- 编码格式: `CSS`
- 类汇编软件表达式: `M[x[2] + zext(offset)][63:0] = f[rs2][63:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `101` |
| `inst[12:7]` | `offset[5:3|8:6]` |
| `inst[6:2]` | `rs2` |
| `inst[1:0]` | `10` |

- 详细说明:
  - 以 `sp` 为基址的压缩双精度 store。

### C.SWSP rs2, offset(sp)

- 指令形式: `C.SWSP rs2, offset(sp)`
- 所属扩展: `C`
- 编码格式: `CSS`
- 类汇编软件表达式: `M[x[2] + zext(offset)][31:0] = x[rs2][31:0]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `110` |
| `inst[12:7]` | `offset[5:2|7:6]` |
| `inst[6:2]` | `rs2` |
| `inst[1:0]` | `10` |

- 详细说明:
  - 以 `sp` 为基址的压缩 word store。

### C.SDSP rs2, offset(sp)

- 指令形式: `C.SDSP rs2, offset(sp)`
- 所属扩展: `C`
- 编码格式: `CSS`
- 类汇编软件表达式: `M[x[2] + zext(offset)][63:0] = x[rs2]`
- inst bit range:

| Inst bit range | Field |
|---|---|
| `inst[15:13]` | `111` |
| `inst[12:7]` | `offset[5:3|8:6]` |
| `inst[6:2]` | `rs2` |
| `inst[1:0]` | `10` |

- 详细说明:
  - RV64C 以 `sp` 为基址的压缩 doubleword store。
