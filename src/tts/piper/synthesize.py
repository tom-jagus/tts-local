"""Run Piper's CLI with a bounded ONNX inference thread count."""

import sys

import onnxruntime


def configure_inference(threads: int) -> None:
    original_session = onnxruntime.InferenceSession

    def configured_session(*args, **kwargs):
        options = kwargs.get("sess_options") or onnxruntime.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        kwargs["sess_options"] = options
        return original_session(*args, **kwargs)

    # This process runs only Piper. Its public loader does not expose session
    # options, so supply them at the runtime boundary before loading the model.
    onnxruntime.InferenceSession = configured_session


def main() -> None:
    threads = int(sys.argv.pop(1))
    configure_inference(threads)
    from piper.__main__ import main as piper_main

    piper_main()


if __name__ == "__main__":
    main()
