// Lucide-style line icon set, ported verbatim from the design bundle's data.jsx.
// Keep this in sync with /tmp/vstudio-design/cmds/project/data.jsx if the design
// evolves.

import type { SVGProps } from 'react';

export type IconName =
  | 'library'
  | 'plus'
  | 'palette'
  | 'settings'
  | 'search'
  | 'bell'
  | 'help'
  | 'upload'
  | 'download'
  | 'chevron'
  | 'check'
  | 'sparkles'
  | 'book'
  | 'file'
  | 'image'
  | 'question'
  | 'layers'
  | 'split'
  | 'logout'
  | 'arrow-l'
  | 'arrow-r'
  | 'docx'
  | 'pdf'
  | 'md'
  | 'regen'
  | 'eye'
  | 'more'
  | 'filter'
  | 'grid'
  | 'list'
  | 'clock'
  | 'shield'
  | 'key'
  | 'user'
  | 'wand'
  | 'play';

type Props = { name: IconName; size?: number; className?: string } & Omit<
  SVGProps<SVGSVGElement>,
  'name' | 'className'
>;

export function Icon({ name, size = 18, className, ...rest }: Props) {
  const common = {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.75,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    className,
    ...rest,
  };

  switch (name) {
    case 'library':  return <svg {...common}><path d="M3 5h18M5 5v14M19 5v14M9 5v14M14 5l2 14M3 19h18"/></svg>;
    case 'plus':     return <svg {...common}><path d="M12 5v14M5 12h14"/></svg>;
    case 'palette':  return <svg {...common}><circle cx="12" cy="12" r="9"/><circle cx="7.5" cy="10.5" r="1"/><circle cx="12" cy="7" r="1"/><circle cx="16.5" cy="10.5" r="1"/><path d="M13 17a2 2 0 0 0 2 2c1.5 0 3-1.5 3-3 0-2-3-2-3-4"/></svg>;
    case 'settings': return <svg {...common}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/></svg>;
    case 'search':   return <svg {...common}><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>;
    case 'bell':     return <svg {...common}><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10 21a2 2 0 0 0 4 0"/></svg>;
    case 'help':     return <svg {...common}><circle cx="12" cy="12" r="9"/><path d="M9.1 9.5a3 3 0 1 1 4.6 2.7c-.9.6-1.7 1.1-1.7 2.3"/><circle cx="12" cy="17.5" r=".5" fill="currentColor"/></svg>;
    case 'upload':   return <svg {...common}><path d="M12 16V4M6 10l6-6 6 6"/><path d="M4 20h16"/></svg>;
    case 'download': return <svg {...common}><path d="M12 4v12M6 10l6 6 6-6"/><path d="M4 20h16"/></svg>;
    case 'chevron':  return <svg {...common}><path d="m9 6 6 6-6 6"/></svg>;
    case 'check':    return <svg {...common}><path d="M5 12l5 5L20 7"/></svg>;
    case 'sparkles': return <svg {...common}><path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/></svg>;
    case 'book':     return <svg {...common}><path d="M4 4h11a4 4 0 0 1 4 4v12H8a4 4 0 0 1-4-4Z"/><path d="M4 16a4 4 0 0 1 4-4h11"/></svg>;
    case 'file':     return <svg {...common}><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>;
    case 'image':    return <svg {...common}><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/></svg>;
    case 'question': return <svg {...common}><circle cx="12" cy="12" r="9"/><path d="M9.1 9.5a3 3 0 1 1 4.6 2.7c-.9.6-1.7 1.1-1.7 2.3"/><circle cx="12" cy="17.5" r=".5" fill="currentColor"/></svg>;
    case 'layers':   return <svg {...common}><path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/><path d="m3 18 9 5 9-5"/></svg>;
    case 'split':    return <svg {...common}><rect x="3" y="4" width="8" height="16" rx="1"/><rect x="13" y="4" width="8" height="16" rx="1"/></svg>;
    case 'logout':   return <svg {...common}><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><path d="M10 17l5-5-5-5"/><path d="M15 12H3"/></svg>;
    case 'arrow-l':  return <svg {...common}><path d="m15 6-6 6 6 6"/></svg>;
    case 'arrow-r':  return <svg {...common}><path d="m9 6 6 6-6 6"/></svg>;
    case 'docx':     return <svg {...common}><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/><path d="M8 13h2l1 4 1-3 1 3 1-4h2"/></svg>;
    case 'pdf':      return <svg {...common}><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/><path d="M9 13v5M9 13h2a1.5 1.5 0 0 1 0 3H9"/></svg>;
    case 'md':       return <svg {...common}><rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 15v-6l2 3 2-3v6M15 9v6M15 15l-2-2M15 15l2-2"/></svg>;
    case 'regen':    return <svg {...common}><path d="M4 4v6h6"/><path d="M20 20v-6h-6"/><path d="M20 9A8 8 0 0 0 6.3 6M4 15a8 8 0 0 0 13.7 3"/></svg>;
    case 'eye':      return <svg {...common}><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12Z"/><circle cx="12" cy="12" r="3"/></svg>;
    case 'more':     return <svg {...common}><circle cx="6" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="18" cy="12" r="1.5"/></svg>;
    case 'filter':   return <svg {...common}><path d="M3 5h18l-7 9v6l-4-2v-4z"/></svg>;
    case 'grid':     return <svg {...common}><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>;
    case 'list':     return <svg {...common}><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>;
    case 'clock':    return <svg {...common}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>;
    case 'shield':   return <svg {...common}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/></svg>;
    case 'key':      return <svg {...common}><circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M16 7l3 3"/></svg>;
    case 'user':     return <svg {...common}><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>;
    case 'wand':     return <svg {...common}><path d="m4 20 12-12"/><path d="m14 6 4 4"/><path d="M20 4v4M22 6h-4M10 2v2M11 3h-2M4 14v2M5 15H3"/></svg>;
    case 'play':     return <svg {...common}><path d="M6 4l14 8-14 8z"/></svg>;
    default: return null;
  }
}

// Google "G" logo for the Continue with Google button.
export function GoogleG({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48">
      <path fill="#FFC107" d="M43.6 20.5H42V20.4H24v7.2h11.3c-1.5 4.2-5.5 7.2-10.3 7.2A11 11 0 1 1 32 16.3l5.1-5.1A18.2 18.2 0 0 0 24 5.8 18.2 18.2 0 1 0 42.2 24c0-1.2-.1-2.4-.4-3.5z"/>
      <path fill="#FF3D00" d="m6.3 14.7 5.9 4.3A11 11 0 0 1 24 13a11 11 0 0 1 8 3.3l5.1-5.1A18.2 18.2 0 0 0 24 5.8 18.2 18.2 0 0 0 6.3 14.7z"/>
      <path fill="#4CAF50" d="M24 42.2A18.2 18.2 0 0 0 36.2 37l-5.6-4.7A11 11 0 0 1 24 35a11 11 0 0 1-10.3-7.2l-5.9 4.5A18.2 18.2 0 0 0 24 42.2z"/>
      <path fill="#1976D2" d="M43.6 20.5H42V20.4H24v7.2h11.3a11 11 0 0 1-3.7 5.1l5.6 4.7c-.4.4 6-4.4 6-13.4 0-1.2-.1-2.4-.4-3.5z"/>
    </svg>
  );
}
