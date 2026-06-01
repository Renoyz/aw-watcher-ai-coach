# aw-coach 截图理解方案选型 — 基于当前机器配置

> 机器型号：Legion Y7000-IRX9  
> 时间：2026-05-30  
> 评估人：Kimi Code

---

## 一、当前机器配置

```
CPU:     Intel Core i7-13650HX (14C/20T, 13th Gen)
内存:    32 GB DDR5 (当前可用 ~21 GB)
GPU:     NVIDIA GeForce RTX 4060 Laptop, 8 GB VRAM
         当前显存占用: 733 MB / 8192 MB
         可用显存: ~7.4 GB
磁盘:    938 GB NVMe SSD (已用 83 GB, 可用 808 GB)
系统:    Ubuntu 22.04.5 LTS
CUDA:    13.0 (Driver 580.159.03)
```

**配置评估：这是一台中高端开发本，完全有能力运行本地 OCR + 轻量 VLM。**

| 能力 | 评估 |
|------|------|
| 本地 OCR (PaddleOCR GPU) | ✅ 完全无压力 |
| 本地 OCR (RapidOCR CPU) | ✅ 完全无压力 |
| 本地 VLM 7B INT4 (Ollama) | ✅ 可运行，显存刚好够用 |
| 本地 VLM 7B INT8 (Ollama) | ⚠️ 可能显存紧张 |
| 本地 VLM 14B+ | ❌ 8GB 显存不够 |
| 同时运行 OCR + VLM + aw-coach daemon | ⚠️ 需要显存/内存管理 |

---

## 二、推荐方案

### 结论先行

> **第一阶段：PaddleOCR (GPU) + triggered screenshot，不做 VLM。**  
> **第二阶段：Ollama + Qwen2.5-VL 7B (INT4) 作为疑难 fallback。**

理由：你的机器配置足够强，PaddleOCR GPU 版可以充分利用 RTX 4060 的 Tensor Core，中文/英文/代码场景准确率最高。8GB 显存虽然可以跑 7B VLM，但 aw-coach 是后台常驻 daemon，不应长期占用显存。

---

## 三、详细选型对比

### 3.1 OCR 方案对比

| 方案 | 中文效果 | 代码/英文 | 速度 | 显存占用 | 部署难度 | 推荐度 |
|------|---------|----------|------|---------|---------|--------|
| **PaddleOCR GPU** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 快 | ~1-2 GB | 中 | **🥇 首选** |
| RapidOCR CPU | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 中等 | 0 | 低 | 🥈 备选 |
| Tesseract | ⭐⭐⭐ | ⭐⭐⭐ | 中等 | 0 | 低 | 🥉 fallback |
| EasyOCR | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 中等 | ~1 GB | 低 | 原型可用 |

**为什么选 PaddleOCR GPU：**

1. **你的机器有 RTX 4060**，PaddleOCR 的 PP-OCRv5 在 GPU 上推理速度极快（单张截图 < 100ms）
2. **中文场景 PaddleOCR 是目前开源最强**，对 IDE 中文界面、技术文档混排支持最好
3. **代码文本识别准确率高**，对终端报错、VSCode 代码窗口的识别明显优于 Tesseract
4. **32GB 内存 + 8GB 显存完全够用**，不会成为瓶颈

**部署方式：**

```bash
# 安装 PaddlePaddle GPU 版
pip install paddlepaddle-gpu

# 安装 PaddleOCR
pip install paddleocr

# 验证 GPU 可用
python -c "import paddle; print(paddle.utils.run_check())"
```

**显存占用预估：**

```text
PaddleOCR 检测模型 (DB):     ~500 MB
PaddleOCR 识别模型 (CRNN):   ~800 MB
合计:                        ~1.3 GB

RTX 4060 可用显存: 7.4 GB
剩余: 6.1 GB (足够再跑一个 7B INT4 VLM)
```

---

### 3.2 VLM 方案对比

| 方案 | 参数 | 显存需求 | 你的机器能否运行 | 推荐度 |
|------|------|---------|----------------|--------|
| **Qwen2.5-VL 7B INT4** | 7B | ~5-6 GB | ✅ 可以 | **🥇 首选** |
| Qwen2.5-VL 3B INT4 | 3B | ~3-4 GB | ✅ 很轻松 | 🥈 备选 |
| MiniCPM-V 4.5 | 8B | ~6-7 GB | ⚠️ 紧张 | 研究可用 |
| Qwen2.5-VL 7B INT8 | 7B | ~8-9 GB | ❌ 不够 | 不推荐 |
| InternVL 78B | 78B | >40 GB | ❌ 远远不够 | 不适用 |

**为什么选 Qwen2.5-VL 7B INT4：**

1. **Ollama 一行命令部署**，`ollama run qwen2.5vl:7b`，零配置
2. **INT4 量化后 ~5-6 GB 显存**，和你的 RTX 4060 8GB 完美匹配
3. **截图理解能力足够**，能判断"这是 IDE 还是终端"、"是否有报错"、"这是什么工作状态"
4. **响应速度可接受**，单张截图推理 ~2-5 秒（GPU）

**为什么不默认启用 VLM：**

```text
aw-coach 是后台常驻 daemon
如果每分钟都调用 VLM，RTX 4060 会长期高负载
风扇噪音、耗电、发热都会成为问题
VLM 应该只在 "triggered" 场景调用，而不是高频扫描
```

---

## 四、推荐架构

### 第一版 screen_sensor（立即实施）

```yaml
screen_understanding:
  enabled: false          # 默认关闭，用户手动开启
  mode: triggered_only    # 绝不定时扫描

  ocr:
    engine: paddleocr
    use_gpu: true         # 利用 RTX 4060
    gpu_mem_limit_mb: 1536

  vlm:
    engine: none          # 第一版不做 VLM

  triggers:
    - unknown_block_duration_gt: 10min
    - low_confidence_duration_gt: 15min
    - death_loop_detected: true

  never_capture_apps:
    - WeChat
    - Feishu
    - 1Password
    - Bitwarden

  store_raw_image: false
  store_ocr_text: true
  capture_cooldown_minutes: 10
```

### 第二版（验证 OCR 有价值后再做）

```yaml
screen_understanding:
  enabled: true
  mode: triggered_only

  ocr:
    engine: paddleocr
    use_gpu: true

  vlm:
    engine: ollama
    model: qwen2.5vl:7b
    ollama_url: http://localhost:11434
    max_calls_per_day: 10   # 严格限制 VLM 调用次数

  triggers:
    - unknown_block_duration_gt: 10min
    - low_confidence_duration_gt: 15min
    - death_loop_detected: true
    - ocr_text_confidence_lt: 0.5   # OCR 结果不够时才调用 VLM
```

---

## 五、显存管理策略

你的 RTX 4060 只有 8GB 显存，需要谨慎管理：

```
显存分配建议:
┌─────────────────────────────────────┐
│ Xorg + GNOME + Firefox + VSCode    │ ~700 MB (系统常驻)
│ PaddleOCR (按需加载)                │ ~1.3 GB (触发时加载，空闲时释放)
│ Qwen2.5-VL 7B INT4 (按需加载)       │ ~5.5 GB (触发时加载，空闲时释放)
│ 预留缓冲                            │ ~1.0 GB
└─────────────────────────────────────┘
```

**关键设计：**

1. **OCR 模型按需加载**：不是 daemon 启动就加载，而是触发截图时才初始化
2. **VLM 按需加载**：Ollama 可以 `ollama run` 后推理完就退出，或者保持服务但用 `--gpu-layers` 控制显存
3. **互斥策略**：OCR 和 VLM 不会同时运行，先 OCR，如果 OCR 不够再调用 VLM
4. **显存监控**：如果 `nvidia-smi` 显示显存不足，自动降级为 CPU OCR 或跳过 VLM

---

## 六、实施顺序

```
Week 1: PaddleOCR 环境搭建 + 截图触发器
  [Day 1] 安装 paddlepaddle-gpu + paddleocr，验证 GPU 可用
  [Day 2] 实现 screen_sensor 模块（截图 + OCR）
  [Day 3] 实现 trigger 逻辑（unknown / low confidence / death loop）
  [Day 4] 集成到 Detector 层，生成 ScreenContext
  [Day 5] 测试真实场景（VSCode / Terminal / Chrome / ChatGPT）

Week 2（可选）: Ollama + Qwen2.5-VL
  [Day 1] 安装 Ollama，下载 qwen2.5vl:7b
  [Day 2] 实现 VLM fallback 逻辑
  [Day 3] 测试疑难场景（RViz / 地图 / 监控面板 / 图表）
  [Day 4-5] 集成到 Research Loop / Agent Inbox
```

---

## 七、风险与应对

| 风险 | 可能性 | 影响 | 应对 |
|------|--------|------|------|
| PaddleOCR GPU 初始化慢 | 中 | 首次截图延迟 3-5 秒 | 模型预加载 + 延迟触发 |
| RTX 4060 风扇噪音 | 中 | 用户体验差 | VLM 严格限制调用频率 |
| OCR 识别代码准确率不足 | 低 | ScreenContext 质量差 | 用 VLM fallback |
| 显存不足导致 OOM | 低 | 系统崩溃 | 显存监控 + 自动降级 CPU |
| 截图触发误报 | 中 | 不必要的 OCR/VLM 调用 | 调整 trigger 阈值 |

---

## 八、一句话结论

> **你的机器（i7-13650HX + 32GB + RTX 4060 8GB）完全有能力运行 PaddleOCR GPU + Qwen2.5-VL 7B INT4。**
>
> **推荐路线：**
> 1. **现在就做 PaddleOCR GPU 版 triggered screenshot** — 这是性价比最高的增强
> 2. **验证有价值后再加 Qwen2.5-VL 7B** — 作为 OCR 不够时的 fallback
> 3. **绝不每分钟全屏扫描** — 只在 Detector 触发时调用
>
> 这样 aw-coach 就能从 "app + title + url" 升级到 "app + title + url + screen_text"，对 unknown / low confidence / death loop 场景的理解能力会有显著提升。
