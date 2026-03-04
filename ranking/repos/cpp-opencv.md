# opencv/opencv

| Field | Value |
|-------|-------|
| **URL** | https://github.com/opencv/opencv |
| **License** | Apache-2.0 |
| **Language** | C++ |
| **Scale** | Large (multi-team project) |
| **Category** | Computer vision library |

## Why this repo

- **No single developer knows it all**: Image processing (filtering, transforms,
  color conversion), feature detection (SIFT, ORB, BRISK), object detection
  (cascade classifiers, DNN module), video analysis (optical flow, tracking),
  camera calibration, 3D reconstruction, highgui (display/interaction),
  machine learning (SVM, k-NN, decision trees, DNN inference), CUDA/OpenCL
  acceleration — each a deep, domain-specific subsystem.
- **Well-structured**: Modules under `modules/` with each module having
  `include/`, `src/`, and `test/` directories. Clear module boundaries
  with dependency declarations between them.
- **Rich history**: 35K+ commits, 25+ years of development. Massive
  contributor base. PRs cover algorithm implementations, performance
  tuning, hardware acceleration, and platform support.
- **Permissive**: Apache-2.0 (since OpenCV 4.5.4).

## Structure overview

```
modules/
├── core/                    # Core data structures and utilities
│   ├── include/opencv2/core/
│   │   ├── mat.hpp          # Mat (image matrix) type
│   │   ├── types.hpp        # Basic types (Point, Rect, Size)
│   │   └── utility.hpp      # Utility functions
│   └── src/
│       ├── matrix.cpp       # Mat implementation
│       ├── arithm.cpp       # Arithmetic operations
│       └── parallel.cpp     # Parallel execution
├── imgproc/                 # Image processing
│   └── src/
│       ├── filter.cpp       # Blur, sharpen, edge detection
│       ├── color.cpp        # Color space conversion
│       ├── geometry.cpp     # Geometric transforms
│       └── histogram.cpp    # Histogram operations
├── features2d/              # Feature detection and matching
├── objdetect/               # Object detection
├── dnn/                     # Deep neural network inference
│   └── src/
│       ├── dnn.cpp          # DNN module core
│       ├── layers/          # Layer implementations (~50 types)
│       └── onnx/            # ONNX model import
├── video/                   # Video analysis (optical flow, tracking)
├── calib3d/                 # Camera calibration, 3D reconstruction
├── highgui/                 # Display and user interaction
├── ml/                      # Traditional machine learning
├── photo/                   # Computational photography
├── stitching/               # Image stitching
└── videoio/                 # Video capture and writing
```

## Scale indicators

- ~3,000 C++ header/source files
- ~1M+ lines of code
- Deep module hierarchies (4-5 levels)
- Cross-cutting core types (Mat, InputArray) used everywhere

## Notes

- Some modules use CUDA/OpenCL for GPU acceleration. These code paths
  are structurally complex but codeplane should index the C++ portions
  well. The `contrib` repo (opencv_contrib) is separate and excluded.

---

## Tasks

10 tasks (3 narrow, 4 medium, 3 wide) for the C++ computer vision library.

## Narrow

### N1: Fix `cv::resize` INTER_AREA producing artifacts at non-integer scale factors

When using `INTER_AREA` interpolation with non-integer downscale factors
(e.g., 0.3x), the resized image shows periodic banding artifacts. The
area interpolation kernel does not handle partial pixel contributions
correctly at fractional boundaries. Fix the area interpolation to
properly weight partial pixel contributions.

### N2: Add `cv::rotate` support for arbitrary angle rotation

`cv::rotate` currently only supports 90°, 180°, and 270° rotations.
Add support for arbitrary angle rotation with configurable interpolation
method and border handling. Compute the output image size to fully
contain the rotated image without clipping.

### N3: Fix `cv::VideoCapture` memory leak when repeatedly opening/closing

Creating and releasing `VideoCapture` objects in a loop leaks memory
because the FFmpeg backend does not fully release codec contexts on
close. The leak grows at ~100KB per open/close cycle. Fix the FFmpeg
backend's release logic to free all allocated codec and format contexts.

## Medium

### M1: Implement ONNX model quantization support in DNN module

The DNN module loads ONNX models but does not support INT8 quantized
models. Implement INT8 inference for quantized ONNX models. Add
quantized versions of common layers (Conv, MatMul, Linear) with
INT8 compute and FP32 accumulation. Support per-channel and per-tensor
quantization. Add a calibration API that computes quantization
parameters from a representative dataset.

### M2: Add GPU memory management for CUDA operations

Implement explicit GPU memory management for CUDA-accelerated
operations. Add a GPU memory pool that pre-allocates memory and serves
allocations from the pool, reducing cudaMalloc overhead. Support
configurable pool size limits, memory fragmentation monitoring, and
automatic pool cleanup. Add per-stream memory tracking for debugging
memory issues.

### M3: Implement image augmentation pipeline

Add a composable image augmentation pipeline for ML training data
preparation. Support: random crop, random flip, random color jitter
(brightness, contrast, saturation, hue), random affine transform,
random erasing, Gaussian blur, and Cutout/CutMix. The pipeline should
be configurable via a builder pattern, serializable to YAML for
reproducibility, and parallelizable across CPU cores.

### M4: Add automatic EXIF orientation handling

Implement automatic EXIF orientation detection and correction in
`cv::imread`. Detect the EXIF orientation tag and automatically
rotate/flip the image to the correct display orientation. Add an
`IMREAD_ORIENT` flag (default ON) that controls this behavior. Support
EXIF orientation in JPEG, TIFF, WebP, and HEIC formats.

## Wide

### W1: Implement zero-copy interop with NumPy/PyTorch/TensorFlow

Add zero-copy data sharing between `cv::Mat` and ML framework tensors.
Implement `cv::Mat` ↔ NumPy `ndarray` (via Python buffer protocol
with no copy), `cv::Mat` ↔ PyTorch `Tensor` (via `__cuda_array_interface__`
for GPU, `__array_interface__` for CPU), and `cv::Mat` ↔ TensorFlow
`Tensor`. Handle stride layout differences, dtype mapping, and GPU
device context. Add convenience functions for common ML preprocessing
patterns.

### W2: Add comprehensive video processing pipeline

Implement a video processing pipeline framework. Support: frame-by-frame
processing with callbacks, multi-stream input (synchronized cameras),
GPU-accelerated decode/encode (NVDEC/NVENC), pipeline parallelism
(decode → process → encode on separate threads), frame rate conversion,
temporal filtering (denoising across frames), and scene change
detection. Add a builder-pattern API for constructing pipelines.

### W3: Implement WebAssembly build with browser-native APIs

Port OpenCV.js (the WASM build) to use modern browser APIs. Replace
the current Emscripten-only build with: WebGPU compute shaders for
GPU-accelerated operations, WebCodecs for hardware video decode,
SharedArrayBuffer for multi-threaded Mat operations, SIMD.js for
vectorized image processing, and OffscreenCanvas for rendering.
Support tree-shaking so users can include only the modules they need.
Add TypeScript type definitions.
