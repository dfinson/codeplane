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

30 tasks (10 narrow, 10 medium, 10 wide).

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

### N4: Fix `cv::imread` not supporting 16-bit PNG with alpha channel

When reading a 16-bit RGBA PNG file with `cv::imread` using
`IMREAD_UNCHANGED`, the alpha channel is silently discarded. The PNG
decoder in `modules/imgcodecs/src/grfmt_png.cpp` only preserves alpha
for 8-bit images. Fix the PNG reader to retain the alpha channel for
16-bit images and return a 4-channel `CV_16UC4` Mat.

### N5: Fix `cv::warpAffine` producing black border artifacts at image edges

When using `cv::warpAffine` with `BORDER_REFLECT`, single-pixel black
lines appear at the image edges. The reflection calculation has an
off-by-one error at the boundary. Fix the border interpolation logic.

### N6: Add `cv::equalizeHistColor` for color histogram equalization

`cv::equalizeHist` only works on grayscale images. Add a convenience
function that converts to YCrCb, equalizes only the Y channel, and
converts back. Preserve the color saturation.

### N7: Fix `cv::VideoWriter` on Linux not honoring FPS for MJPEG codec

When writing MJPEG video on Linux with `VideoWriter`, the output file
metadata shows the requested FPS but the actual frame timing is wrong
because the MJPEG container doesn't embed per-frame timestamps. Fix
the MJPEG writer to embed correct frame durations.

### N8: Fix `cv::calcHist` ignoring mask for multi-channel images

When computing a histogram with `cv::calcHist` on a multi-channel
image with a mask, the mask is applied correctly for single-channel
images but ignored for multi-channel inputs. The histogram calculation
in `modules/imgproc/src/histogram.cpp` skips the mask check in the
multi-channel code path. Fix the multi-channel histogram loop to
respect the mask.

### N9: Add `cv::connectedComponentsWithContours` for combined labeling and contour extraction

Currently connected component labeling (`connectedComponents`) and
contour finding (`findContours`) are separate operations that must be
run sequentially. Add a combined function that produces both labeled
regions and their contours in a single pass.

### N10: Fix `cv::dnn::readNet` memory leak when loading invalid ONNX models

When `readNet` fails to parse an invalid ONNX file, the partially
constructed network layers are not freed. The error path skips the
cleanup of already-allocated layer buffers. Fix the error cleanup path.

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

### M4: Add multi-format image batch decoding API

Implement `cv::decodeBatch(buffers)` that decodes multiple images in
parallel using a thread pool. Each buffer is routed to the appropriate
format decoder based on magic bytes via the existing `findDecoder`
mechanism in `modules/imgcodecs/src/loadsave.cpp`. Support
heterogeneous formats within a single batch. Add configurable
thread count and optional GPU-accelerated JPEG decoding. Return
partial results when individual images fail to decode.

### M5: Implement image tiling and reassembly for large image processing

Add `cv::TileProcessor` that splits large images into overlapping
tiles, processes each tile independently (with configurable overlap
for seamless stitching), and reassembles the result. Support
configurable tile size, overlap region, and blending mode (linear,
feather). Enable parallel tile processing via the `parallel_for_`
infrastructure in `modules/core/src/parallel.cpp`. Support both
CPU and GPU (UMat) processing paths.

### M6: Add AVIF image format read/write support

Add AVIF (AV1 Image File Format) codec support to
`modules/imgcodecs/`. Implement `AvifDecoder` and `AvifEncoder`
classes following the existing codec pattern (e.g., `grfmt_png.cpp`).
Register the codec with the decoder/encoder factory in
`loadsave.cpp`. Support 8-bit and 10-bit images, alpha channel,
and ICC color profile embedding. Add signature-based format detection
for AVIF files.

### M7: Implement video stabilization module

Add a video stabilization pipeline: feature detection across frames,
motion estimation (affine or homography), motion smoothing (Gaussian
or Kalman filter), and compensatory frame warping. Support real-time
processing. Include quality metrics (stability score, crop ratio).

### M8: Add image quality assessment metrics

Implement image quality assessment functions: SSIM (structural
similarity), PSNR (peak signal-to-noise ratio), BRISQUE (blind
image quality), and NIQE (natural image quality). Support batch
evaluation for comparing sets of images. Include GPU-accelerated
paths for SSIM and PSNR.

### M9: Add DNN model input preprocessing pipeline

Implement a configurable preprocessing pipeline for DNN inference
that chains common operations: resize, crop, color conversion,
normalization (mean subtraction, scale), and channel reordering
(HWC→CHW). Define preprocessing configs via `cv::dnn::PreprocessParams`
struct. Integrate with `cv::dnn::Net::setInput` to apply preprocessing
automatically. Support loading preprocessing configs from ONNX model
metadata. Changes span `modules/dnn/src/dnn.cpp` and
`modules/dnn/include/opencv2/dnn/dnn.hpp`.

### M10: Add structured video annotation support

Implement a video annotation data structure that associates per-frame
metadata (bounding boxes, labels, keypoints, segmentation masks) with
video frames. Support serialization to/from JSON and protocol buffers.
Include rendering annotations onto frames with configurable styles.

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

### W4: Implement WebGPU compute backend for browser deployment

Add a WebGPU compute backend alongside the existing CUDA and OpenCL
backends. Implement core operations (matrix multiply, convolution,
resize, color conversion) as WebGPU compute shaders. Support the DNN
module's inference path. Integrate with the Emscripten/WASM build.
Changes span the HAL (hardware abstraction layer), operation dispatch,
DNN backend, and build system.

### W5: Implement hardware-accelerated video transcoding pipeline

Add a video transcoding pipeline that leverages hardware acceleration
(NVENC/NVDEC, VA-API, VideoToolbox) for decode → filter → encode
workflows. Support zero-copy GPU frame transfer between decode and
encode stages. Add a filter graph for applying imgproc operations
(resize, color conversion, overlay) on GPU frames without CPU
readback. Changes span `modules/videoio/` (hardware codec backends),
`modules/core/` (GPU buffer management), `modules/imgproc/` (GPU
filter paths), and add a transcoding pipeline module.

### W6: Implement model optimization and deployment toolkit

Add tools for DNN model deployment: model quantization (FP32→FP16→INT8
with calibration), operator fusion (Conv+BN+ReLU), dead layer pruning,
input shape inference, and model benchmarking with per-layer timing.
Support ONNX model manipulation. Changes span the DNN module's
optimization passes, layer implementations, quantization infrastructure,
and add benchmarking tools.

### W7: Add 3D point cloud processing module

Implement `cv::ppf3d` replacement with modern algorithms: point cloud
I/O (PLY, PCD, OBJ), point cloud filtering (voxel grid, SOR, radius
outlier), registration (ICP, RANSAC, feature-based), surface
reconstruction (Poisson, ball pivoting), and visualization. Support
organized and unorganized point clouds. Changes span core data
structures, add I/O handlers, algorithm implementations, and
visualization.

### W8: Implement federated learning support for vision models

Add privacy-preserving distributed training for CV models. Support
federated averaging across multiple DNN training sessions, differential
privacy noise injection, secure aggregation protocol, and model
compression for communication efficiency. Include a coordinator server
and worker client. Changes span the DNN training infrastructure,
add networking, privacy mechanisms, and aggregation protocols.

### W9: Add automated test generation for image processing functions

Implement a testing framework that automatically generates test inputs
for image processing functions: edge cases (empty images, 1-pixel
images, extreme aspect ratios), numerical boundary cases (overflow,
underflow), format variations (all supported depths and channel
counts), and property-based testing (verify invariants like
resize(resize(img, s1), s2) ≈ resize(img, s1*s2)). Changes span
the test infrastructure, add input generators, property definitions,
and a test runner.

### W10: Implement real-time object tracking with re-identification

Add a multi-object tracking pipeline: detection (from DNN module),
track initialization, motion prediction (Kalman filter), data
association (Hungarian, IoU-based), track lifecycle management
(tentative/confirmed/lost), and re-identification (feature-based
matching for track recovery after occlusion). Include evaluation
metrics (MOTA, IDF1). Changes span the DNN module, add a tracking
module, association algorithms, and evaluation tools.
