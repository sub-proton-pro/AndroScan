import { useEffect, useRef, useState } from "react";
import { getDeviceStatus, type DeviceStatus } from "../api/device";
import { DeviceBoot } from "./DeviceBoot";
import { IconChevronRight } from "./Icons";

type Props = {
  onTap: (xDevice: number, yDevice: number) => void;
  /** Optional: render a collapse button in the mirror header. Mirrors the
   *  Projects sidebar pattern so the parent can shrink this column to a
   *  vertical rail. */
  onCollapse?: () => void;
  /** Currently selected AndroScan app — used by the "Bring device online"
   *  wizard so it can install + launch the app once the emulator is up. */
  appId?: string | null;
};

const STATUS_POLL_MS = 3000;

/**
 * Live emulator/device mirror via the existing /ws/mirror WebSocket (PNG
 * frames). Click anywhere on the image and we translate the click from image
 * pixels to device pixels and forward to ``onTap`` so the parent can call
 * /api/inspect/map.
 */
export function MirrorView({ onTap, onCollapse, appId = null }: Props) {
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [device, setDevice] = useState<DeviceStatus>({ online: false, state: "…", detail: "" });
  const [imgError, setImgError] = useState<string | null>(null);
  const [naturalSize, setNaturalSize] = useState<{ w: number; h: number } | null>(null);
  const lastUrlRef = useRef<string | null>(null);

  // ----- "Bring device online" wizard popover -----
  //
  // The wizard now lives in a small popover anchored *under the device
  // status indicator* in the header. Visibility model:
  //
  //   * ``popoverOpen``  — explicit open state. Toggled by clicking the
  //     status pill or hovering it. Cleared by clicking outside, the X
  //     close button, or pressing Escape. Auto-opens once whenever the
  //     device transitions from online → offline (so a fresh disconnect
  //     surfaces the wizard without the user having to reach for it).
  //   * ``wizardLocked`` — true while ``DeviceBoot.run()`` is in flight.
  //     Forces the popover to stay open and disables the outside-click
  //     dismiss so a stray click doesn't unmount the wizard mid-install.
  //
  // We deliberately do NOT auto-dismiss after a successful run: the user
  // asked for "stay open until the user clicks elsewhere".
  const [wizardLocked, setWizardLocked] = useState(false);
  const [popoverOpen, setPopoverOpen] = useState(false);
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const prevOnlineRef = useRef<boolean | null>(null);

  // Poll device status.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      const s = await getDeviceStatus();
      if (!cancelled) setDevice(s);
    };
    tick();
    const id = window.setInterval(tick, STATUS_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  // Auto-open the popover on the *first* probe that confirms the device
  // is offline, and on every subsequent online→offline transition. We
  // gate on ``device.state !== "…"`` so the placeholder initial state
  // doesn't trigger a flash-open before the first probe lands.
  useEffect(() => {
    if (device.state === "…") return;
    if (prevOnlineRef.current === null) {
      if (!device.online) setPopoverOpen(true);
    } else if (prevOnlineRef.current && !device.online) {
      setPopoverOpen(true);
    }
    prevOnlineRef.current = device.online;
  }, [device.online, device.state]);

  // Outside-click + Escape dismiss. Suppressed while the wizard is mid-
  // run (``wizardLocked``) so a misclick can't unmount install/launch.
  useEffect(() => {
    if (!popoverOpen || wizardLocked) return;
    const onDocMouseDown = (e: MouseEvent) => {
      const node = anchorRef.current;
      if (node && e.target instanceof Node && !node.contains(e.target)) {
        setPopoverOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPopoverOpen(false);
    };
    document.addEventListener("mousedown", onDocMouseDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocMouseDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [popoverOpen, wizardLocked]);

  // WebSocket mirror.
  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}/ws/mirror`);
    ws.binaryType = "blob";
    ws.onmessage = (ev) => {
      if (ev.data instanceof Blob) {
        const url = URL.createObjectURL(ev.data);
        if (imgRef.current) imgRef.current.src = url;
        if (lastUrlRef.current) URL.revokeObjectURL(lastUrlRef.current);
        lastUrlRef.current = url;
      } else if (typeof ev.data === "string") {
        try {
          const msg = JSON.parse(ev.data);
          if (msg?.type === "error") setImgError(msg.message ?? "mirror error");
        } catch {
          /* ignore */
        }
      }
    };
    ws.onerror = () => setImgError("mirror websocket error");
    ws.onclose = () => setImgError((prev) => prev ?? "mirror disconnected");
    return () => {
      ws.close();
      if (lastUrlRef.current) URL.revokeObjectURL(lastUrlRef.current);
      lastUrlRef.current = null;
    };
  }, []);

  // Drop the rendered frame as soon as the device goes offline. Without
  // this, the last good screencap keeps showing for the entire offline
  // window — misleading the operator into thinking the device is still
  // up (the green status pill is the only clue otherwise). We DO NOT
  // close the WebSocket: the backend keeps polling ``adb screencap`` and
  // sends ``type=error`` JSON frames while disconnected, then resumes
  // sending PNGs the moment the device is back. Reopening the socket on
  // every offline/online transition would thrash the connection and add
  // a multi-second blank window after recovery.
  //
  // Resetting ``naturalSize`` to null also re-shows the .mirror-empty
  // placeholder ("Device offline — open the status pill above…") which
  // the operator needs to discover the boot wizard.
  useEffect(() => {
    if (device.online) return;
    setNaturalSize(null);
    setImgError(null);
    if (imgRef.current) imgRef.current.removeAttribute("src");
    if (lastUrlRef.current) {
      URL.revokeObjectURL(lastUrlRef.current);
      lastUrlRef.current = null;
    }
  }, [device.online]);

  const handleClick = (e: React.MouseEvent<HTMLImageElement>) => {
    const img = imgRef.current;
    if (!img || !naturalSize) return;
    const rect = img.getBoundingClientRect();
    const sx = (e.clientX - rect.left) / rect.width;
    const sy = (e.clientY - rect.top) / rect.height;
    onTap(Math.round(sx * naturalSize.w), Math.round(sy * naturalSize.h));
  };

  const showWizard = popoverOpen || wizardLocked;

  const handleWizardComplete = () => {
    // Per the user's request the popover stays open until they click
    // elsewhere; we only release the lock so outside-click dismiss starts
    // working again. The green ✓ on the launch step persists.
    setWizardLocked(false);
  };

  const handleWizardDismiss = () => {
    setWizardLocked(false);
    setPopoverOpen(false);
  };

  return (
    <div className="mirror-view">
      <header className="mirror-head">
        {/*
          The status pill is the anchor for the boot-wizard popover. The
          wrapping <div> is the hover/click target *and* the bounding box
          used by the outside-click dismiss check, so the popover lives
          inside it (cursor moving from pill → popover never crosses an
          outside boundary).
        */}
        <div
          className="device-status-anchor"
          ref={anchorRef}
          onPointerEnter={() => setPopoverOpen(true)}
        >
          <button
            type="button"
            className="device-status-button"
            onClick={() => setPopoverOpen((v) => !v)}
            aria-haspopup="dialog"
            aria-expanded={showWizard}
            title={`device: ${pillStateLabel(device)} (${pillSubtitle(device)})`}
          >
            {/*
              Three pill states:
                * green dot, "online"  — adbd up + sys.boot_completed=1
                * amber dot, "booting" — adbd up but boot still in progress
                * red dot, "offline"   — adbd unreachable (or no device)
              The amber tier is the new one — without it the operator
              can't tell whether the install/launch will run cleanly or
              race the half-up OS.
            */}
            <span className={`status-dot ${pillDotClass(device)}`} aria-hidden />
            <span className="mirror-status muted small">
              device: <strong>{pillStateLabel(device)}</strong>{" "}
              <span className="muted">({pillSubtitle(device)})</span>
            </span>
          </button>
          {showWizard && (
            <div
              className="device-boot-popover"
              role="dialog"
              aria-label="Bring device online wizard"
            >
              <DeviceBoot
                appId={appId}
                detail={device.detail || device.device_name || device.state}
                deviceOnline={device.online}
                bootCompleted={device.boot_completed ?? false}
                onRunStart={() => setWizardLocked(true)}
                onComplete={handleWizardComplete}
                onDismiss={handleWizardDismiss}
              />
            </div>
          )}
        </div>
        {naturalSize && (
          <span className="muted small">
            {naturalSize.w}×{naturalSize.h}
          </span>
        )}
        {onCollapse && (
          <button
            type="button"
            className="ghost-mini icon-btn mirror-collapse-btn"
            onClick={onCollapse}
            title="Collapse mirror panel"
            aria-label="Collapse mirror panel"
          >
            <IconChevronRight />
          </button>
        )}
      </header>
      <div className="mirror-frame">
        <img
          ref={imgRef}
          alt=""
          // Keep the element mounted (the WebSocket effect needs the ref
          // and onLoad must fire on the first frame) but hide it until a
          // real frame is decoded — otherwise the empty <img> renders its
          // alt text in the same centered position as the .mirror-empty
          // overlay, producing the visible text-on-text overlap.
          style={{
            visibility: naturalSize ? "visible" : "hidden",
            cursor: naturalSize ? "crosshair" : "default",
          }}
          onClick={naturalSize ? handleClick : undefined}
          onLoad={(e) => {
            const t = e.currentTarget;
            if (t.naturalWidth && t.naturalHeight) {
              setNaturalSize({ w: t.naturalWidth, h: t.naturalHeight });
              setImgError(null);
            }
          }}
        />
        {!naturalSize && (
          // Mirror frame placeholder while we wait for the first PNG.
          // The wizard now lives in a header popover, so it never gets
          // rendered in here anymore — the frame stays clean.
          <p className="muted small mirror-empty">
            {device.online
              ? imgError ?? "Waiting for first frame from /ws/mirror…"
              : "Device offline — open the status pill above to bring it online."}
          </p>
        )}
      </div>
      <p className="muted small mirror-tip">Tap an element in the mirror to map it to code.</p>
    </div>
  );
}

/** Render-friendly subtitle for the status pill — prefers the resolved
 *  AVD / model name (e.g. ``Pixel_2_API_28``) over the raw adb state
 *  token (``device``, ``offline``, …). Falls back to the state when the
 *  backend couldn't resolve a name (no device, multi-device-attached
 *  error, brand-new physical device the model getprop fails on).
 *  Also used by the button ``title=`` so the tooltip stays in sync
 *  with the visible label without duplicating the precedence logic. */
function pillSubtitle(device: DeviceStatus): string {
  const name = (device.device_name ?? "").trim();
  if (device.online && name) return name;
  return device.state;
}

/** Human-readable status word for the pill. Distinguishes the "adbd is
 *  up but Android isn't ready yet" state from the fully-booted state
 *  so the operator doesn't misread an amber-but-not-green pill as a
 *  ready-to-install device. */
function pillStateLabel(device: DeviceStatus): "online" | "booting" | "offline" {
  if (!device.online) return "offline";
  if (device.boot_completed === false) return "booting";
  return "online";
}

/** CSS modifier for the status dot. ``ok`` (green) only when fully
 *  booted; ``warn`` (amber) for the boot-in-progress window; ``err``
 *  (red) when adbd is unreachable. The ``warn`` class is reused from
 *  existing toast styling so no new CSS is required. */
function pillDotClass(device: DeviceStatus): "ok" | "warn" | "err" {
  if (!device.online) return "err";
  if (device.boot_completed === false) return "warn";
  return "ok";
}
