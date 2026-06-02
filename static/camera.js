window.startCamera = async function () {
  const video = document.getElementById("video");

  if (!video) {
    console.error("Video element not found");
    return;
  }

  try {
    if (video.srcObject) {
      video.srcObject.getTracks().forEach(track => track.stop());
      video.srcObject = null;
    }

    const stream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: "user",
        width: { ideal: 1280 },
        height: { ideal: 720 }
      }
    });

    video.srcObject = stream;

    await video.play();

    console.log("Camera started successfully");

    if (typeof startMediaPipe === "function") {
      startMediaPipe(video);
    }

  } catch (err) {
    console.error("Camera error:", err);
  }
};