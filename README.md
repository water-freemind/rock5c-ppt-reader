# ROCK 5C PPT 阅读助手

嵌入式 Linux 课程设计。在 ROCK 5C 上采集 PPT 画面，通过 RK3588 NPU 识别整页文字，校对后生成摘要，整理课堂并发送笔记到 Windows。

## 使用流程

1. 在「页面工作台」导入图片或用 IMX219 拍照。整页识别后可编辑原文，也可以框选漏字区域补识。
2. 想连续采集时，开启「自动采集」。相册支持多选、批量 OCR、保存原文、综合摘要、删除和恢复。
3. 在工作台或相册点击「加入课堂」，可新建课堂或追加到已有课堂。同一页的不同来源共用一条历史记录。综合摘要中的页码可以打开生成时的原图和原文。
4. 摘要默认使用本地 Qwen3-4B，部署对应权重后可切换 Qwen3-8B。也可在工作台选择 DeepSeek API，云端模式会把待摘要文字发给服务端。
5. 在 Windows 打开接收端，选择接收文件夹并开始监听。板端发送的笔记保存为 Markdown 和 JSON，发送失败可在板端队列中查看和重试。

## 报告源码索引

| 模块 | 关键文件 |
| --- | --- |
| 图像采集、曝光与自动去重 | [camera.py](camera.py)、[camera_exposure.py](camera_exposure.py)、[auto_capture.py](auto_capture.py) |
| 图像校正与整页 OCR | [image_processing.py](image_processing.py)、[ocr_engine.py](ocr_engine.py)、[reading_order.py](reading_order.py)、[ocr_comparison.py](ocr_comparison.py) |
| 本地与云端摘要 | [npu_summary.py](npu_summary.py)、[rkllm_probe123.cpp](rkllm_probe123.cpp)、[deepseek_summary.py](deepseek_summary.py) |
| 桌面界面、相册与课堂 | [app.py](app.py)、[ui_layout.py](ui_layout.py)、[capture_album.py](capture_album.py)、[collections_view.py](collections_view.py)、[collection_summary.py](collection_summary.py) |
| 存储、导出与发送 | [records.py](records.py)、[page_evidence.py](page_evidence.py)、[outbox.py](outbox.py)、[tcp_transport.py](tcp_transport.py)、[receiver.py](receiver.py) |

[工程说明与报告索引](docs/implementation.md)说明数据流、关键实现和验证范围。

## 板端运行

复现环境为 ROCK 5C、Debian ARM64、Python 3.8.20、RKNN Toolkit Lite2 2.3.0、RKLLM Runtime 1.2.3 和 NPU 驱动 0.9.8。相机通过 V4L2 取帧。Python 环境需具备 Tkinter，运行依赖见 [requirements-board.txt](requirements-board.txt)。RKNN Lite2 使用与板端 Python 版本、架构匹配的官方 wheel，也可使用课程包提供的对应 wheel。摄像头曝光调节使用 v4l2-ctl。未连接相机时，可以导入图片处理。

| 依赖 | 工程内的位置 |
| --- | --- |
| RKNN ARM64 运行库 | `librknnrt.so` |
| RK3588 PP-OCRv4 检测和识别模型 | `models/ppocrv4_det_rk3588.rknn`、`models/ppocrv4_rec_rk3588.rknn` |
| RKLLM 1.2.3 SDK 头文件、运行库 | `experimental/rkllm123/include/rkllm.h`、`experimental/rkllm123/lib/librkllmrt.so` |
| 默认本地摘要权重 | `models/Qwen3-4B_RK3588_w8a8.rkllm` |
| 可选本地摘要权重 | `models/Qwen3-8B_RK3588_w8a8.rkllm` |

模型标识、权重来源和校验值见 [model_candidates.json](model_candidates.json)。使用 [Rockchip PP-OCR 示例](https://github.com/airockchip/rknn_model_zoo/tree/v2.3.0/examples/PPOCR/PPOCR-System)的 PP-OCRv4 ONNX 时，将文件放在工程根目录，命名为 `ppocrv4_det.onnx` 和 `ppocrv4_rec.onnx`，在 RKNN Toolkit 2.3.0 转换环境执行 `python convert_ocr.py`。转换工具和板端推理环境可以分别配置。参考 [Radxa ROCK 5C AI 文档](https://docs.radxa.com/rock5/rock5c/app-development/ai)和 [Rockchip RKLLM SDK](https://github.com/airockchip/rknn-llm)。

在项目根目录执行，示例假设将板端 Python 环境置于 `env/`。

```bash
env/bin/python build_npu_runner.py
env/bin/python app.py
```

构建 C++ 启动器需要 g++。通过 SSH 启动已有 VNC 桌面时，可用 `XAUTHORITY="$HOME/.Xauthority" env/bin/python launch_gui.py :1`，其中 `:1` 替换为实际显示编号。界面「连接设置」可以修改摄像头设备节点和电脑接收地址。

## Windows 接收端

Windows 上安装带 Tkinter 的 Python 后，双击 [start-receiver-windows.cmd](start-receiver-windows.cmd)。该脚本监听本机回环地址，适用于 SSH 反向转发，默认接收目录为工程内的 `work/received`。使用局域网直连时，通过 `receiver.py --gui --host` 指定 Windows 的局域网地址，在板端的连接设置填写同一地址，默认 TCP 端口为 5088。接收窗口可选择实际保存目录。

## 验证

在具备板端 Python 依赖的环境下运行离线回归。包含 Tk 的检查需在图形会话中运行，课堂和历史窗口测试使用受控输入。板端实测范围见工程说明。

```bash
env/bin/python -m unittest test_core test_auto_capture test_page_records \
  test_outbox test_reading_order test_review_workflow test_summary_format \
  test_ocr_comparison test_collections test_history_gui
```

仓库提供应用源码及测试，模型、板端环境、动态库和实际课程记录在各自环境准备。PP-OCR 字典和后处理保留原许可及来源，见 [LICENSE.official](vendor/ocr_conda_lab/LICENSE.official) 和 [official_sources.json](vendor/ocr_conda_lab/official_sources.json)。模型和 SDK 使用各自的上游许可。
