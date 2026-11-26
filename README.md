# ROCK 5C PPT 阅读助手

嵌入式 Linux 课程设计。用 ROCK 5C 拍摄或导入 PPT 图片，把整页文字识别出来，校对后生成笔记。

## 工程概览

- **识别** · 板端用 PP-OCRv4 和 NPU 提取整页文字，识别结果可直接修改。
- **整理** · 校对后可用板端 Qwen3 或 DeepSeek 生成摘要。单页笔记可保存到历史，多页可加入课堂并生成带来源页码的综合摘要。
- **分享** · 笔记可导出为 Markdown 或 TXT，也可通过 TCP 发到 Windows 接收端。发送失败时，记录留在队列里等待重试。

已部署的板子可从桌面打开「PPT 阅读助手」。第一次部署请看下方的[板端运行](#板端运行)和 [Windows 接收端](#windows-接收端)。想看代码，从板端的 [board_app](board_app)、电脑的 [windows_receiver](windows_receiver) 和两边共用的 [shared](shared) 开始。

- [简要使用教程（Word）](docs/guides/quick-start.docx)介绍识别、课堂整理、笔记导出和 Windows 接收。
- [详细 Linux 教程（Word）](docs/guides/linux-tutorial.docx)按步骤讲解工程目录、摄像头、NPU 推理、数据保存、通信与故障排查。
- [功能截图](docs/screenshots/README.md)提供板端和接收端的实机画面及图注。

## 使用流程

1. 在「页面工作台」导入图片或用 IMX219 拍照后，点击「识别文字」。可以直接校对文字。断行多时点「AI 整理段落」，预览后应用。原图里尚能读清的漏字可用区域补识。
2. 想连续采集时，开启「自动采集」。相册支持多选、批量 OCR、保存原文、综合摘要、删除和恢复。
3. 在工作台或相册点击「加入课堂」，可新建课堂或追加到已有课堂。在「课堂整理」顶部选择生成模型，再点「生成综合摘要」。长课件可超过五条要点，并保留各要点对应的页码。点击「打开原页」默认查看单页摘要，可切到「OCR 原文」整理段落或恢复原始换行。
4. 摘要默认使用本地 Qwen3-4B，部署对应权重后可切换 Qwen3-8B。工作台与课堂整理共享生成模型选择，课堂底部的「已保存的综合摘要」按模型查看历史结果。也可选择 DeepSeek API，云端模式会把待摘要文字发给服务端。
5. 「历史记录」按图片合并同页的不同摘要来源。「导出笔记」支持 Markdown 和 TXT。发送到 Windows 时，先在电脑接收端选择目录并开始监听。Windows 接收端保存 Markdown 和 JSON。发送失败可在板端队列中查看和重试。

### 区域补识

整页 OCR 漏掉一小块文字时，可以对原图中那一块单独再跑一次 NPU OCR。适合补页脚、表格里的一个单元格，或尺寸较小的数字和单位。

1. 在「识别原文」点击漏字应该接在哪一行，把光标放在那一行。
2. 在左侧图片上依次点击目标区域的两个对角，然后点击「区域补识」。
3. 检查结果，确认后点「插入正文」。补识内容会插入光标所在行之后。

少量错字可直接修改，换行太碎时用「AI 整理段落」。源图文字模糊或过曝时，重新拍摄更容易获得可识别的输入。

## 目录结构

```text
board_app/         ROCK 5C 板端 GUI 与业务
  capture/        V4L2 摄像头、曝光与自动采集
  ocr/            整页识别、图像校正与阅读顺序
  summary/        本地 NPU、DeepSeek 和多页摘要
  storage/        课堂分组与 OCR 证据
  ui/             工作台、相册、历史及课堂界面
windows_receiver/ Windows 接收端
shared/           板端和接收端共用的记录格式与 TCP 协议
tools/            模型转换、C++ 推理入口和板端启动
tests/            回归测试
docs/             工程说明与报告索引
vendor/           PP-OCR 字典和上游后处理
licenses/         上游 SDK 与模型许可
```

Python 负责 GUI、数据处理、文件保存和网络通信。OCR 由 Python 通过 RKNN Lite 调用 NPU。板端本地摘要由 Python 启动 [C++ 推理程序](tools/rkllm_probe123.cpp)，后者通过 RKLLM 调用 NPU。

## 报告源码索引

| 模块 | 关键文件 |
| --- | --- |
| 图像采集、曝光与自动去重 | [camera.py](board_app/capture/camera.py)、[camera_exposure.py](board_app/capture/camera_exposure.py)、[auto_capture.py](board_app/capture/auto_capture.py) |
| 图像校正与整页 OCR | [image_processing.py](board_app/ocr/image_processing.py)、[ocr_engine.py](board_app/ocr/ocr_engine.py)、[reading_order.py](board_app/ocr/reading_order.py)、[ocr_comparison.py](board_app/ocr/ocr_comparison.py) |
| 区域补识与段落整理 | [app.py](board_app/app.py)、[review_workflow.py](board_app/ui/review_workflow.py)、[review_format.py](board_app/summary/review_format.py) |
| 本地与云端摘要 | [npu_summary.py](board_app/summary/npu_summary.py)、[rkllm_probe123.cpp](tools/rkllm_probe123.cpp)、[deepseek_summary.py](board_app/summary/deepseek_summary.py) |
| 桌面界面、相册与课堂 | [app.py](board_app/app.py)、[ui_layout.py](board_app/ui/ui_layout.py)、[capture_album.py](board_app/ui/capture_album.py)、[collections_view.py](board_app/ui/collections_view.py)、[collection_summary.py](board_app/summary/collection_summary.py) |
| 存储、导出与发送 | [records.py](shared/records.py)、[page_evidence.py](board_app/storage/page_evidence.py)、[outbox.py](board_app/outbox.py)、[tcp_transport.py](shared/tcp_transport.py)、[receiver.py](windows_receiver/receiver.py) |

[工程说明与报告索引](docs/implementation.md)说明数据流、关键实现和验证范围。

## 板端运行

复现环境为 ROCK 5C、Debian ARM64、Python 3.8.20、RKNN Toolkit Lite2 2.3.0、RKLLM Runtime 1.2.3 和 NPU 驱动 0.9.8。相机通过 V4L2 取帧。Python 环境需具备 Tkinter，运行依赖见 [board_app/requirements.txt](board_app/requirements.txt)。RKNN Lite2 使用与板端 Python 版本、架构匹配的官方 wheel，也可使用课程包提供的对应 wheel。摄像头曝光调节使用 v4l2-ctl。未连接相机时，可以导入图片处理。

| 依赖 | 工程内的位置 |
| --- | --- |
| RKNN ARM64 运行库 | `librknnrt.so` |
| RK3588 PP-OCRv4 检测和识别模型 | `models/ppocrv4_det_rk3588.rknn`、`models/ppocrv4_rec_rk3588.rknn` |
| RKLLM 1.2.3 SDK 头文件、运行库 | `experimental/rkllm123/include/rkllm.h`、`experimental/rkllm123/lib/librkllmrt.so` |
| 默认本地摘要权重 | `models/Qwen3-4B_RK3588_w8a8.rkllm` |
| 可选本地摘要权重 | `models/Qwen3-8B_RK3588_w8a8.rkllm` |

模型标识、权重来源和校验值见 [model_candidates.json](tools/model_candidates.json)。使用 [Rockchip PP-OCR 示例](https://github.com/airockchip/rknn_model_zoo/tree/v2.3.0/examples/PPOCR/PPOCR-System)的 PP-OCRv4 ONNX 时，将文件放在工程根目录，命名为 `ppocrv4_det.onnx` 和 `ppocrv4_rec.onnx`，在 RKNN Toolkit 2.3.0 转换环境执行 `python -m tools.convert_ocr`。转换工具和板端推理环境可以分别配置。参考 [Radxa ROCK 5C AI 文档](https://docs.radxa.com/rock5/rock5c/app-development/ai)和 [Rockchip RKLLM SDK](https://github.com/airockchip/rknn-llm)。

在项目根目录执行，示例假设将板端 Python 环境置于 `env/`。

```bash
env/bin/python -m tools.build_npu_runner
env/bin/python -m board_app
```

构建 C++ 启动器需要 g++。首次部署或更新 C++ 启动器源码后，运行上述构建命令。通过 SSH 启动已有 VNC 桌面时，可用 `env/bin/python -m tools.launch_gui :1`，其中 `:1` 替换为实际显示编号。界面「连接设置」可以修改摄像头设备节点和电脑接收地址。

## Windows 接收端

Windows 上安装带 Tkinter 的 Python 后，双击 [start-receiver-windows.cmd](start-receiver-windows.cmd)。该脚本监听本机回环地址，适用于 SSH 反向转发，默认接收目录为工程内的 `work/received`。局域网直连时，在仓库根目录运行 `py -3 -m windows_receiver.receiver --gui --host <Windows局域网地址>`，将地址替换为实际值，并在板端填写同一地址。默认 TCP 端口为 5088。接收窗口可选择实际保存目录。

## 验证

在具备板端 Python 依赖的环境下运行离线回归。包含 Tk 的检查需在图形会话中运行，课堂和历史窗口测试使用受控输入。板端实测范围见工程说明。

```bash
env/bin/python -m unittest discover -s tests -t .
```

仓库提供应用源码及测试，模型、板端环境、动态库和实际课程记录在各自环境准备。PP-OCR 字典和后处理保留原许可及来源，见 [LICENSE.official](vendor/ocr_conda_lab/LICENSE.official) 和 [official_sources.json](vendor/ocr_conda_lab/official_sources.json)。模型和 SDK 使用各自的上游许可。
