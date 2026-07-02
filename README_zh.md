# cpp2il.github.io
Unity CPP2IL is a reverse-engineering platform for Unity IL2CPP. It processes APK, IPA, WASM, ELF, and Mach-O packages — extracting metadata, rebuilding type hierarchies, restoring call graphs and control flow, then decompiles to readable C# with a traceable IR pipeline. Designed for game security research, code auditing, and compatibility analysis

[English document](README.md) | [中文文档](README_zh.md) 

<h1 align="center">Unity CPP2IL</h1>

<p align="center">
  <strong>面向 Unity IL2CPP 的多平台逆向还原工作台</strong>
</p>

<p align="center">
  <a href="https://cpp2il.com">官网</a> ·
  <a href="https://ccna3po7lqul.feishu.cn/share/base/form/shrcnSKePcBYvea3LNF4h8wgvII">内测申请</a> ·
  <a href="https://github.com/chenzifeng/cpp2il.github.io/issues">问题反馈</a> ·
  <a href="#-常见问题">FAQ</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Unity-IL2CPP-blue?logo=unity" alt="Unity IL2CPP" />
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey" alt="Platform" />
  <img src="https://img.shields.io/badge/Status-Beta-orange" alt="Status" />
  <img src="https://img.shields.io/badge/License-Proprietary-red" alt="License" />
</p>

[![视频封面](https://i0.hdslb.com/bfs/archive/你的封面图.jpg)](https://www.bilibili.com/video/BV1vm7W6gEjL)
[Unity CPP2IL 产品演示](https://www.bilibili.com/video/BV1Rm7W6uEJM)

---

## 目录

- [简介](#简介)
- [核心能力](#核心能力)
- [支持的输入格式](#支持的输入格式)
- [工作流程](#工作流程)
- [输出结果](#输出结果)
- [快速开始](#快速开始)
  - [在线平台（推荐）](#在线平台推荐)
  - [本地部署](#本地部署)
- [使用示例](#使用示例)
  - [示例 1：分析 Android APK](#示例-1分析-android-apk)
  - [示例 2：分析 iOS IPA](#示例-2分析-ios-ipa)
  - [示例 3：分析 WebGL WASM](#示例-3分析-webgl-wasm)
- [项目结构](#项目结构)
- [技术架构](#技术架构)
  - [分析管线](#分析管线)
  - [AST 还原链路](#ast-还原链路)
- [与同类工具对比](#与同类工具对比)
- [性能基准](#性能基准)
- [常见问题](#常见问题)
- [安全与合规](#安全与合规)
- [贡献指南](#贡献指南)
- [更新日志](#更新日志)
- [联系方式](#联系方式)
- [许可证](#许可证)

---

## 简介

**Unity CPP2IL** 是一个面向 Unity IL2CPP 项目的逆向分析与 C# 代码还原平台。

Unity 引擎使用 IL2CPP（Intermediate Language To C++）技术将 C# 代码编译为 C++，再由平台原生编译器生成机器码。这一过程丢失了原始的类型信息、泛型参数、委托关系与高层控制流结构。CPP2IL 的目标是从编译产物中尽可能还原这些信息，输出可读、可追踪的 C# 代码与结构化分析报告。

### 适用场景

| 场景 | 说明 |
|---|---|
| **游戏安全研究** | 分析游戏逻辑、协议通信、数据存储机制 |
| **代码审计** | 检查第三方 SDK 行为、隐私合规、敏感数据处理 |
| **兼容性排查** | 定位崩溃堆栈中 IL2CPP 符号对应的原始代码 |
| **漏洞分析** | 还原潜在的安全漏洞上下文 |
| **学术研究** | 编译器逆向、程序分析、静态分析技术研究 |

> **免责声明**：本工具仅供安全研究与代码审计用途。使用者应遵守所在地区的法律法规，不得用于非法目的。

---

## 核心能力

- **IL2CPP 元数据解析** — 解析 `global-metadata.dat`，提取类型定义、方法签名、字符串字面量、字段布局等元数据
- **libil2cpp 二进制分析** — 基于 ELF / PE / Mach-O / WASM 二进制结构，识别 IL2CPP 生成的 C++ 函数与运行时接口
- **类型结构重建** — 还原类继承关系、接口实现、泛型实例化、枚举定义与嵌套类型
- **调用关系恢复** — 构建方法级调用图（Call Graph），识别虚方法调度、委托调用与反射调用
- **控制流分析** — 从 LLVM IR / 机器码层面恢复 if-else、循环、switch、try-catch 等控制流结构
- **C# 代码还原** — 输出接近 Unity 工程习惯的 C# 代码，保留命名空间、类型层次与方法签名
- **AST 可追踪** — 每段输出代码可追溯到中间表示（IR）节点，便于人工审查与验证
- **多平台覆盖** — 统一分析管线，支持 Android、iOS、Windows、macOS、Linux、WebGL 等平台产物

---

## 支持的输入格式

| 格式 | 平台 | 说明 |
|---|---|---|
| `.apk` | Android | 完整 APK 包，自动解压并定位 `libil2cpp.so` 与 `global-metadata.dat` |
| `.aab` | Android | Android App Bundle |
| `.ipa` | iOS | iOS 应用包，自动解压并定位 Mach-O 二进制 |
| `.xarchive` / `.app` | macOS | macOS 应用包 |
| `.exe` / `.dll` | Windows | Windows 可执行文件或 IL2CPP 动态库 |
| `.so` / `.dylib` | Linux / macOS | ELF 或 Mach-O 共享库 |
| `.wasm` | WebGL | WebAssembly 模块，配合 JavaScript 胶水代码分析 |
| `libil2cpp.so` + `global-metadata.dat` | 通用 | 直接上传核心二进制与元数据文件 |

> 上传时无需手动解包。平台会自动识别文件类型、运行平台与元数据结构。

---

## 工作流程

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│              │     │              │     │              │     │              │
│  ① 上传包体  │────▶│ ② 自动识别   │────▶│ ③ 结构还原   │────▶│ ④ 结果导出   │
│              │     │              │     │              │     │              │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
  APK / IPA /          文件类型识别         类型重建             C# 代码
  WASM / ELF /         平台判定            方法签名恢复         结构报告
  Mach-O / EXE         元数据定位          调用图构建           分析日志
                       反编译策略选择       控制流分析
                                           AST 生成
```

### 详细步骤

**Step 1 — 上传包体与元数据**

支持拖拽或选择文件上传。平台支持单文件（APK/IPA）或分离文件（libil2cpp.so + global-metadata.dat）两种上传模式。

**Step 2 — 自动识别分析目标**

- 检测文件格式（ELF / PE / Mach-O / WASM）
- 识别目标架构（ARMv7 / ARM64 / x86 / x86_64 / WebAssembly）
- 定位 IL2CPP 元数据区段与符号表
- 解析 Unity 版本以选择对应的元数据结构定义
- 根据平台特征选择最优分析策略

**Step 3 — 恢复结构与调用关系**

- 解析 `global-metadata.dat` 中的类型定义表、方法定义表、字符串表
- 在二进制中定位 IL2CPP Runtime API 调用点
- 重建类层次结构（继承链、接口实现）
- 恢复方法签名（参数类型、返回值、泛型参数）
- 构建方法级调用图
- 从 LLVM IR / 机器码恢复高层控制流
- 生成抽象语法树（AST）

**Step 4 — 查看并导出结果**

- 在线浏览还原后的 C# 代码
- 按命名空间 / 类型 / 方法层级导航
- 查看方法级调用关系图
- 导出完整项目结构（ZIP）
- 导出分析报告（Markdown / JSON）

---

## 输出结果

### C# 代码

输出的 C# 代码尽可能还原以下结构：

```csharp
// 还原示例 — 输出代码保留命名空间、类型层次、泛型参数与方法签名
namespace Game.Core
{
    public class PlayerController : MonoBehaviour
    {
        private Rigidbody _rigidbody;
        private float _moveSpeed = 5.0f;

        public void Move(Vector3 direction)
        {
            _rigidbody.velocity = direction * _moveSpeed;
        }

        private void Update()
        {
            float h = Input.GetAxis("Horizontal");
            float v = Input.GetAxis("Vertical");
            Move(new Vector3(h, 0f, v));
        }
    }
}
```

### 结构化报告

```
├── types.json          # 类型定义（字段、方法、继承关系）
├── methods.json        # 方法签名与调用关系
├── strings.json        # 字符串字面量表
├── callgraph.json      # 方法级调用图
├── analysis.log        # 分析过程日志
└── summary.md          # 可读摘要报告
```

### 可追踪 IR

每个还原的 C# 语句可关联到中间表示节点：

```
[Line 12] _rigidbody.velocity = direction * _moveSpeed;
  ├─ IR: StoreField(offset=0x28, type=Rigidbody)
  ├─ IR: BinaryOp(Mul, param_1, FieldLoad(offset=0x18))
  └─ Source: IL2CPP_icall UnityEngine_Rigidbody_set_velocity
```

---

## 快速开始

### 在线平台（推荐）

1. 访问 [cpp2il.com](https://cpp2il.com)
2. 点击「申请内测」填写申请表
3. 获得访问权限后，上传包体文件
4. 等待分析完成，在线浏览或导出结果

### 本地部署

#### 环境要求

| 依赖 | 最低版本 | 说明 |
|---|---|---|
| Docker | 24.0+ | 推荐使用 Docker 部署 |
| 磁盘空间 | 10 GB+ | 取决于分析目标大小 |
| 内存 | 8 GB+ | 大型包体建议 16 GB+ |

#### Docker 部署

```bash
# 拉取镜像
docker pull ghcr.io/your-org/cpp2il:latest

# 启动服务
docker run -d \
  --name cpp2il \
  -p 8080:8080 \
  -v ./data:/app/data \
  ghcr.io/your-org/cpp2il:latest

# 访问
open http://localhost:8080
```

#### 源码构建

```bash
# 克隆仓库
git clone https://github.com/chenzifeng/cpp2il.github.io.git
cd cpp2il

# 安装依赖
npm install

# 构建
npm run build

# 启动
npm run start
```

---

## 使用示例

### 示例 1：分析 Android APK

```bash
# 使用 CLI 工具分析 APK
cpp2il analyze \
  --input game.apk \
  --platform android \
  --arch arm64 \
  --output ./output/

# 输出结构
# ./output/
# ├── GameAssembly.so          # 提取的 IL2CPP 二进制
# ├── global-metadata.dat      # 提取的元数据
# ├── decompiled/              # 还原的 C# 代码
# │   ├── Assembly-CSharp/
# │   │   ├── Game/
# │   │   │   ├── Core/
# │   │   │   │   ├── PlayerController.cs
# │   │   │   │   ├── GameManager.cs
# │   │   │   │   └── ...
# │   │   │   └── UI/
# │   │   │       └── ...
# │   │   └── Plugins/
# │   └── Assembly-CSharp-firstpass/
# ├── report.json              # 分析报告
# └── summary.md               # 可读摘要
```

### 示例 2：分析 iOS IPA

```bash
cpp2il analyze \
  --input game.ipa \
  --platform ios \
  --arch arm64 \
  --output ./output/

# 平台会自动：
# 1. 解压 IPA → Payload/Game.app/
# 2. 定位主二进制文件
# 3. 提取 embedded metadata
# 4. 执行完整分析管线
```

### 示例 3：分析 WebGL WASM

```bash
cpp2il analyze \
  --input build.wasm \
  --platform webgl \
  --glue build.framework.js \
  --output ./output/

# WebGL 分析额外处理：
# - WASM 模块解析
# - JavaScript 胶水代码映射
# - IL2CPP WASM Runtime 接口识别
```

---

## 项目结构

```
cpp2il/
├── packages/
│   ├── core/                  # 核心分析引擎
│   │   ├── metadata/          # 元数据解析器
│   │   │   ├── parser.ts      # global-metadata.dat 解析
│   │   │   ├── types.ts       # 类型定义重建
│   │   │   └── strings.ts     # 字符串表提取
│   │   ├── binary/            # 二进制分析
│   │   │   ├── elf.ts         # ELF 格式支持
│   │   │   ├── pe.ts          # PE 格式支持
│   │   │   ├── macho.ts       # Mach-O 格式支持
│   │   │   └── wasm.ts        # WebAssembly 支持
│   │   ├── decompiler/        # 反编译管线
│   │   │   ├── cfg.ts         # 控制流图构建
│   │   │   ├── ssa.ts         # SSA 变换
│   │   │   ├── type-infer.ts  # 类型推断
│   │   │   └── emitter.ts     # C# 代码生成
│   │   └── callgraph/         # 调用图分析
│   ├── web/                   # Web 前端
│   │   ├── src/
│   │   │   ├── pages/         # 页面组件
│   │   │   ├── components/    # UI 组件
│   │   │   └── stores/        # 状态管理
│   │   └── public/
│   ├── api/                   # 后端 API
│   │   ├── routes/
│   │   ├── services/
│   │   └── workers/           # 分析任务队列
│   └── cli/                   # 命令行工具
├── docs/                      # 文档
├── examples/                  # 示例文件
├── docker-compose.yml
└── README.md
```

---

## 技术架构

### 分析管线

```
                     ┌─────────────────────────────────────────────────┐
                     │              Unity CPP2IL Pipeline              │
                     └─────────────────────────────────────────────────┘
                                          │
                    ┌─────────────────────┼─────────────────────┐
                    ▼                     ▼                     ▼
            ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
            │  Metadata     │     │  Binary       │     │  Glue /      │
            │  Parser       │     │  Analyzer     │     │  Runtime     │
            │              │     │              │     │              │
            │ global-       │     │ ELF/PE/MachO │     │ JS glue      │
            │ metadata.dat │     │ WASM module   │     │ IL2CPP RT    │
            └──────┬───────┘     └──────┬───────┘     └──────┬───────┘
                   │                     │                     │
                   └──────────┬──────────┘─────────────────────┘
                              ▼
                    ┌──────────────────┐
                    │  Type System     │
                    │  Reconstruction  │
                    │                  │
                    │ 类继承 / 接口    │
                    │ 泛型实例化       │
                    │ 枚举 / 委托      │
                    └────────┬─────────┘
                              │
                   ┌──────────┴──────────┐
                   ▼                     ▼
           ┌──────────────┐     ┌──────────────┐
           │  Call Graph   │     │  Control Flow │
           │  Builder      │     │  Recovery     │
           │              │     │              │
           │ 直接调用      │     │ if/else      │
           │ 虚方法调度    │     │ loops        │
           │ 委托 / 反射   │     │ switch       │
           │              │     │ try/catch    │
           └──────┬───────┘     └──────┬───────┘
                   │                     │
                   └──────────┬──────────┘
                              ▼
                    ┌──────────────────┐
                    │  AST Generation  │
                    │  & IR Tracing    │
                    └────────┬─────────┘
                              │
                   ┌──────────┴──────────┐
                   ▼                     ▼
           ┌──────────────┐     ┌──────────────┐
           │  C# Emitter   │     │  Report       │
           │              │     │  Generator    │
           │ 可读 C# 代码  │     │              │
           │ 命名空间组织   │     │ JSON / MD     │
           └──────────────┘     └──────────────┘
```

### AST 还原链路

每段输出代码均可追溯到中间表示：

```
源码 (C#)  →  IL (IL2CPP)  →  C++ (编译产物)  →  机器码  →  [CPP2IL]  →  IR  →  AST  →  C# (还原)
```

CPP2IL 的核心工作是从右侧逆向还原左侧的信息。AST 还原链路保证每个输出节点都可以回溯到对应的 IR 表示，支持人工审查与验证。

---

## 与同类工具对比

| 特性 | CPP2IL | Il2CppDumper | Cpp2IL (Samboy) | dnSpy/ILSpy |
|---|:---:|:---:|:---:|:---:|
| 元数据解析 | ✅ | ✅ | ✅ | ❌ |
| 方法体还原 | ✅ | ❌ | ⚠️ 部分 | ✅ (仅 .NET) |
| 控制流恢复 | ✅ | ❌ | ⚠️ 基础 | ✅ |
| 调用图构建 | ✅ | ❌ | ❌ | ❌ |
| AST 可追踪 | ✅ | ❌ | ❌ | ❌ |
| WebGL 支持 | ✅ | ❌ | ❌ | ❌ |
| 在线平台 | ✅ | ❌ | ❌ | ❌ |
| 多格式统一入口 | ✅ | ❌ | ⚠️ | ❌ |
| 代码结构还原质量 | 高 | 无代码输出 | 中 | 仅限托管代码 |

> **说明**：本对比基于截至 2025 年 5 月的公开信息，各工具持续更新中，实际能力请以最新版本为准。

---

## 性能基准

以下数据基于典型 Unity IL2CPP 项目的测试结果（仅作参考）：

| 指标 | 小型项目 | 中型项目 | 大型项目 |
|---|---|---|---|
| 方法数量 | ~5K | ~50K | ~200K+ |
| 元数据大小 | ~5 MB | ~50 MB | ~200 MB+ |
| libil2cpp 大小 | ~30 MB | ~150 MB | ~500 MB+ |
| 分析耗时 | ~2 min | ~15 min | ~60 min+ |
| 内存占用 | ~2 GB | ~6 GB | ~16 GB+ |

> 具体性能取决于服务器配置、目标复杂度与 IL2CPP 版本。

---

## 常见问题

### Q: 支持哪些 Unity 版本？

支持 Unity 5.6 ~ Unity 6（2025）的 IL2CPP 输出。不同版本的 `global-metadata.dat` 结构存在差异，平台会自动检测并适配。

### Q: 输出的 C# 代码可以直接编译吗？

不完全可以。输出代码还原了类型结构、方法签名与控制流，但以下信息可能缺失或近似：
- 局部变量名（IL2CPP 不保留原始变量名）
- 注释与代码风格
- 某些复杂的泛型约束
- 编译器生成的辅助方法

输出代码的主要价值在于**可读性分析**与**结构理解**，而非直接复用编译。

### Q: 分析结果的准确度如何？

准确度取决于目标的复杂度与 IL2CPP 版本。典型的：
- **类型与方法签名**：准确率 95%+
- **控制流结构**：对简单函数准确率 90%+，复杂嵌套或优化后的函数可能需要人工校正
- **调用关系**：直接调用准确率高，虚方法与反射调用可能存在近似

### Q: 如何处理代码混淆？

平台内置基础的反混淆能力（字符串解密、控制流平坦化还原等）。对于重度混淆的目标，建议结合 IR 追踪功能进行人工分析。

### Q: 分析数据存储在哪里？

- **在线平台**：分析结果加密存储，保留 30 天后自动清除。用户可随时手动删除。
- **本地部署**：数据完全存储在本地，不上传至外部服务器。

### Q: 可以批量分析多个包体吗？

内测阶段暂不支持批量队列。正式版将提供批量分析 API 与任务队列功能。

---

## 安全与合规

### 数据安全

- 所有上传文件使用 TLS 加密传输
- 分析结果加密存储，隔离访问
- 支持手动删除与自动过期清除
- 本地部署模式下数据不出本地网络

### 合规声明

- 本工具仅面向安全研究人员与代码审计人员
- 使用者应确保拥有分析目标的合法权限
- 不得将本工具用于侵犯知识产权、破解商业软件或其他违法行为
- 输出结果仅供研究参考，不构成法律建议

---

## 贡献指南

欢迎社区贡献。当前阶段主要接受以下类型的贡献：

1. **Bug 反馈** — 通过 [Issues](https://github.com/chenzifeng/cpp2il.github.io/issues) 提交
2. **文档改进** — 修正错误、补充说明、翻译
3. **平台适配** — 新增文件格式支持、Unity 版本适配
4. **分析策略** — 改进控制流恢复、类型推断等算法

### 开发流程

```bash
# Fork 本仓库
# 创建特性分支
git checkout -b feature/your-feature

# 提交更改
git commit -m "feat: add xxx support"

# 推送并创建 Pull Request
git push origin feature/your-feature
```

### 代码规范

- TypeScript 严格模式
- ESLint + Prettier 格式化
- 提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/)
- 新功能需附带单元测试

---

## 更新日志

### v0.9.0-beta（2026-01）

- 首次内测发布
- 支持 APK / IPA / WASM / ELF / Mach-O 格式
- 完整的元数据解析 → 类型重建 → 控制流分析 → C# 代码生成管线
- 在线工作台 Web UI
- 基础调用图构建与 IR 追踪

### 计划中

- [ ] CLI 工具公开发布
- [ ] 批量分析 API
- [ ] Unity 6 完整适配
- [ ] 反混淆策略扩展
- [ ] 插件系统（自定义分析策略）
- [ ] VS Code 插件（在线浏览分析结果）

---

## 联系方式

| 渠道 | 链接 |
|---|---|
| 官网 | [cpp2il.com](https://cpp2il.com) |
| 内测申请 | [cpp2il.com/apply.html](https://ccna3po7lqul.feishu.cn/share/base/form/shrcnSKePcBYvea3LNF4h8wgvII) |
| GitHub | [https://github.com/chenzifeng/cpp2il.github.io](https://github.com/chenzifeng/cpp2il.github.io) |
| 问题反馈 | [GitHub Issues](https://github.com/chenzifeng/cpp2il.github.io/issues) |

---

## 许可证

Copyright © 2026 CPP2IL. All rights reserved.

本项目为专有软件。未经授权不得复制、修改或分发。详见 [LICENSE](./LICENSE) 文件。

---

<p align="center">
  <sub>仅供安全研究与代码审计用途。请遵守当地法律法规。</sub>
</p>
