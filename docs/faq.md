# FAQ

## General

??? question "Does it need the internet?"
    Only to install it and to download the model once (`ppe-guard weights download`). After that
    it runs fully offline. Webhook alerts need a connection to the webhook's server.

??? question "Does it send video or data anywhere?"
    No. Video is processed on your machine and stored in your data folder. The only outgoing
    messages are the webhook alerts you configure.

??? question "Does it recognise who a worker is?"
    No. Worker numbers (`#14`) are tracker IDs that last while a person stays in view. There is no
    face recognition or identification.

??? question "Is it free for commercial use?"
    PPE Guard's code is MIT licensed. The model weights derive from Ultralytics YOLO, which is
    AGPL-3.0, so a commercial product or hosted service built on them must follow the AGPL-3.0 or
    use an Ultralytics commercial licence. The dataset is CC BY 4.0. See
    [Models](reference/models.md#licence).

## Hardware

??? question "Do I need a GPU?"
    No, but it helps. A desktop CPU processes about 15 frames per second in total, enough for two
    or three cameras at about 5 fps each; that is plenty, because people move slowly compared to
    the 2-second violation delay. An NVIDIA GPU roughly triples that. See
    [Performance](deployment/performance.md).

??? question "How many cameras can one machine handle?"
    It depends on the GPU, the image size and **Max FPS per camera**. See the measurements in
    [Performance](deployment/performance.md) and lower the frame rate to fit more cameras.

??? question "Does it run on a Jetson or Raspberry Pi?"
    It installs anywhere PyTorch and OpenCV do. A Jetson can run it, ideally with a TensorRT export
    of the model (`ppe-guard export --format engine`). A Raspberry Pi is too slow for live video
    with the default model; use it for occasional photos with `ppe-guard predict`, or try a
    smaller model trained with `ppe-guard train --model yolo26n.pt`.

## Detection

??? question "It misses workers without hard hats"
    Recognising a bare head is the model's weakest class. Try a lower **Confidence** (e.g. 0.3),
    a larger **Image size** for distant workers, and a camera angle that shows heads clearly. For a
    big improvement, fine-tune on your own footage ([Training](reference/training.md)).

??? question "It raises violations that aren't real"
    Raise **Confidence**, raise **Violation delay** (e.g. to 4 seconds), and use [PPE zones](guide/zones.md)
    so areas where PPE isn't needed (offices, roads, neighbours' land) are ignored. Turn off
    **Strict mode** if it is on.

??? question "A worker wearing a vest shows 'Vest: not visible'"
    The model couldn't see the torso, for example behind a railing or a machine. Without strict
    mode that never counts as a violation.

??? question "What does `?3` mean?"
    A bare head (or another missing item) that the model found without finding the person under
    it, usually because the person is partly hidden. It is still checked.

??? question "Can it check other equipment, like gloves, goggles or harnesses?"
    Not with the included models, which know hard hats, vests and masks. You can train a model
    with more classes, but the dashboard's rules only cover those three items today. Contributions
    are welcome.

## Running it

??? question "How do I reach the dashboard from my phone?"
    Set a password (`ppe-guard password set`), start with `ppe-guard serve --host 0.0.0.0`, and
    open `http://<computer's IP>:8000` on a phone on the same network. For access from outside,
    use a VPN or an HTTPS reverse proxy ([Production](deployment/production.md)).

??? question "Where is my data?"
    In the data folder: `./data` where you started the server, `--data-dir`, or
    `PPE_GUARD_DATA_DIR`. **Settings → System** shows the full path.

??? question "I forgot the password"
    On the server, run `ppe-guard password set` (or `ppe-guard password clear`) with the same
    `--data-dir`, then restart the server.

??? question "My camera shows 'Error: could not open source'"
    Check the address in VLC from the same machine. For RTSP cameras check the user name,
    password, port (usually 554) and stream path, and that the camera is reachable from the server
    (and from the container, with Docker). Webcams can be in use by another program.

??? question "Clips don't play in the browser"
    **Settings → System → Video codec** shows what your OpenCV can write. If it says *not playable
    in browsers*, download the clips, or install an OpenCV build with an H.264 or VP8 encoder.
