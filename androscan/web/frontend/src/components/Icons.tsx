/**
 * Tiny outline-style SVG icon set used across the workbench so toolbar
 * glyphs render at consistent pixel sizes and tint with ``currentColor``.
 * Every icon shares the same viewBox and stroke width so they line up
 * visually when placed side-by-side in a button row.
 */

import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & {
  /** Pixel size for both width and height. Defaults to 14. */
  size?: number;
};

function Base({ size = 14, className, children, ...rest }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className ? `icon-svg ${className}` : "icon-svg"}
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

export function IconSearch(props: IconProps) {
  return (
    <Base {...props}>
      <circle cx="7" cy="7" r="4.25" />
      <line x1="10.2" y1="10.2" x2="13.5" y2="13.5" />
    </Base>
  );
}

export function IconGear(props: IconProps) {
  // 8-tooth gear: a single rounded-square rotated 45° inside the spokes
  // would be heavier than needed, so use eight short radial ticks plus
  // the rim and pin.
  const ticks: React.ReactNode[] = [];
  for (let i = 0; i < 8; i += 1) {
    const a = (i * Math.PI) / 4;
    const x1 = 8 + Math.cos(a) * 5.2;
    const y1 = 8 + Math.sin(a) * 5.2;
    const x2 = 8 + Math.cos(a) * 7.0;
    const y2 = 8 + Math.sin(a) * 7.0;
    ticks.push(<line key={i} x1={x1} y1={y1} x2={x2} y2={y2} />);
  }
  return (
    <Base {...props}>
      <circle cx="8" cy="8" r="4.6" />
      <circle cx="8" cy="8" r="1.6" />
      {ticks}
    </Base>
  );
}

export function IconRefresh(props: IconProps) {
  // Open circular arrow with an arrowhead at the top.
  return (
    <Base {...props}>
      <path d="M13.2 8a5.2 5.2 0 1 1 -1.55 -3.7" />
      <polyline points="13.4,2.4 13.4,5.0 10.8,5.0" />
    </Base>
  );
}

export function IconChevronLeft(props: IconProps) {
  return (
    <Base {...props}>
      <polyline points="10,3 5.5,8 10,13" />
    </Base>
  );
}

export function IconChevronRight(props: IconProps) {
  return (
    <Base {...props}>
      <polyline points="6,3 10.5,8 6,13" />
    </Base>
  );
}

export function IconClose(props: IconProps) {
  return (
    <Base {...props}>
      <line x1="4" y1="4" x2="12" y2="12" />
      <line x1="12" y1="4" x2="4" y2="12" />
    </Base>
  );
}

export function IconOpenIn(props: IconProps) {
  // Classic "open in new" / "external link": frame in the bottom-left
  // with an arrow leaving the top-right corner.
  return (
    <Base {...props}>
      <polyline points="11,8.5 11,13 3,13 3,5 7.5,5" />
      <polyline points="9,3 13,3 13,7" />
      <line x1="13" y1="3" x2="7.5" y2="8.5" />
    </Base>
  );
}
