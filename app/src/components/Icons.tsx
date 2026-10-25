// Small inline icon set (stroke icons, 24px grid).
type P = { size?: number };
const S = ({ size = 20, children }: P & { children: React.ReactNode }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden>{children}</svg>
);
export const IconPlus = (p: P) => <S {...p}><path d="M12 5v14M5 12h14" /></S>;
export const IconSearch = (p: P) => <S {...p}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></S>;
export const IconSidebar = (p: P) => <S {...p}><rect x="3" y="4" width="18" height="16" rx="3" /><path d="M9 4v16" /></S>;
export const IconSend = (p: P) => <S {...p}><path d="M12 19V5M6 11l6-6 6 6" /></S>;
export const IconStop = (p: P) => <S {...p}><rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" /></S>;
export const IconChevron = (p: P) => <S {...p}><path d="m6 9 6 6 6-6" /></S>;
export const IconCheck = (p: P) => <S {...p}><path d="m5 12 5 5 9-10" /></S>;
export const IconCopy = (p: P) => <S {...p}><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V6a2 2 0 0 1 2-2h8" /></S>;
export const IconRetry = (p: P) => <S {...p}><path d="M4 12a8 8 0 1 0 2.4-5.7L4 8.5" /><path d="M4 4v4.5h4.5" /></S>;
export const IconGear = (p: P) => <S {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></S>;
export const IconPin = (p: P) => <S {...p}><path d="M12 17v5M9 10.8V4h6v6.8l2.6 3.2H6.4z" /></S>;
export const IconTrash = (p: P) => <S {...p}><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></S>;
export const IconEdit = (p: P) => <S {...p}><path d="M4 20h4L19 9l-4-4L4 16z" /></S>;
export const IconBell = (p: P) => <S {...p}><path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4z" /><path d="M10 21h4" /></S>;
export const IconPower = (p: P) => <S {...p}><path d="M12 3v9" /><path d="M6.3 7.3a8 8 0 1 0 11.4 0" /></S>;
export const IconBrain = (p: P) => <S {...p}><path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 3 3h0V4zM15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-3 3V4z" /></S>;
export const IconTool = (p: P) => <S {...p}><path d="M14.7 6.3a4 4 0 0 0 5 5L13 18a2.8 2.8 0 0 1-4-4z" /><path d="m9 14-5 5" /></S>;
export const IconX = (p: P) => <S {...p}><path d="M6 6l12 12M18 6 6 18" /></S>;
export const IconMic = (p: P) => <S {...p}><rect x="9" y="3" width="6" height="12" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></S>;
export const IconMenu = (p: P) => <S {...p}><path d="M4 7h16M4 12h16M4 17h10" /></S>;
export const IconBolt = (p: P) => <S {...p}><path d="M13 3 5 14h6l-1 7 8-11h-6z" /></S>;
export const IconFolder = (p: P) => <S {...p}><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></S>;
export const IconGrid = (p: P) => <S {...p}><rect x="4" y="4" width="7" height="7" rx="1.5" /><rect x="13" y="4" width="7" height="7" rx="1.5" /><rect x="4" y="13" width="7" height="7" rx="1.5" /><rect x="13" y="13" width="7" height="7" rx="1.5" /></S>;
export const IconSliders = (p: P) => <S {...p}><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0" /><circle cx="16" cy="6" r="2" /><circle cx="10" cy="12" r="2" /><circle cx="18" cy="18" r="2" /></S>;
export const IconGlobe = (p: P) => <S {...p}><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c3 3.2 3 14.8 0 18M12 3c-3 3.2-3 14.8 0 18" /></S>;
export const IconShield = (p: P) => <S {...p}><path d="M12 3 4.5 6v5.5c0 4.6 3.1 8.4 7.5 9.5 4.4-1.1 7.5-4.9 7.5-9.5V6z" /><path d="m9 12 2.2 2.2L15.5 10" /></S>;
export const IconAlert = (p: P) => <S {...p}><path d="M12 3.5 2.5 20h19z" /><path d="M12 10v4.5M12 17.3v.2" /></S>;
export const IconDoc = (p: P) => <S {...p}><path d="M6 3h8l4 4v14H6z" /><path d="M14 3v4h4M9 12h6M9 16h6" /></S>;
export const IconSlides = (p: P) => <S {...p}><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M12 16v4M8 20h8" /></S>;
export const IconImage = (p: P) => <S {...p}><rect x="3" y="4" width="18" height="16" rx="2.5" /><circle cx="9" cy="10" r="1.8" /><path d="m21 16-5-5-8 9" /></S>;
export const IconTable = (p: P) => <S {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 10h18M3 15h18M9 4v16" /></S>;
export const IconTerminal = (p: P) => <S {...p}><rect x="3" y="4" width="18" height="16" rx="2.5" /><path d="m7 9 3 3-3 3M12.5 15H17" /></S>;
export const IconEye = (p: P) => <S {...p}><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></S>;
export const IconPen = (p: P) => <S {...p}><path d="M4 20h4L19.5 8.5a2.8 2.8 0 0 0-4-4L4 16z" /><path d="m13.5 6.5 4 4" /></S>;
export const IconCloud = (p: P) => <S {...p}><path d="M7 18a4.5 4.5 0 0 1-.6-9 6 6 0 0 1 11.4 1.6A3.8 3.8 0 0 1 17.5 18z" /></S>;
export const IconClock = (p: P) => <S {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></S>;
export const IconSparkle = (p: P) => <S {...p}><path d="M12 3c.6 4.7 4.3 8.4 9 9-4.7.6-8.4 4.3-9 9-.6-4.7-4.3-8.4-9-9 4.7-.6 8.4-4.3 9-9z" /></S>;
export const IconBook = (p: P) => <S {...p}><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z" /><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5" /></S>;
export const IconUsers = (p: P) => <S {...p}><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6 6 0 0 1 3.5 6" /></S>;
export const IconMoon = (p: P) => <S {...p}><path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5z" /></S>;
export const IconArrowLeft = (p: P) => <S {...p}><path d="M19 12H5M11 6l-6 6 6 6" /></S>;
export const IconFile = (p: P) => <S {...p}><path d="M6 3h8l4 4v14H6z" /><path d="M14 3v4h4" /></S>;
export const IconCoins = (p: P) => <S {...p}><ellipse cx="9" cy="7" rx="6" ry="3" /><path d="M3 7v5c0 1.7 2.7 3 6 3s6-1.3 6-3V7" /><path d="M9 15v2c0 1.7 2.7 3 6 3s6-1.3 6-3v-5c0-1.6-2.3-2.8-5.3-3" /></S>;
export const IconSlash = (p: P) => <S {...p}><rect x="3" y="3" width="18" height="18" rx="5" /><path d="m14.5 7-5 10" /></S>;
export const IconKeyboard = (p: P) => <S {...p}><rect x="2.5" y="6" width="19" height="12" rx="2.5" /><path d="M6.5 10h.01M10 10h.01M13.5 10h.01M17 10h.01M7 14h10" /></S>;
export const IconExpand = (p: P) => <S {...p}><path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7" /></S>;
export const IconArrowRight = (p: P) => <S {...p}><path d="M5 12h14M13 6l6 6-6 6" /></S>;
export const IconLock = (p: P) => <S {...p}><rect x="5" y="10.5" width="14" height="10" rx="2.5" /><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" /></S>;
export const IconHand = (p: P) => <S {...p}><path d="M8 13V5.5a1.5 1.5 0 0 1 3 0V11M11 10.5v-6a1.5 1.5 0 0 1 3 0v6M14 10.5v-4a1.5 1.5 0 0 1 3 0V14a7 7 0 0 1-7 7h-.6a6 6 0 0 1-4.6-2.2L2.6 15.9a1.5 1.5 0 0 1 2.2-2l3.2 2.6" /></S>;
export const IconList = (p: P) => <S {...p}><path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01" /></S>;
export const IconCrew = (p: P) => <S {...p}><circle cx="12" cy="12" r="3" /><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1" /></S>;
export const IconInfo = (p: P) => <S {...p}><circle cx="12" cy="12" r="9" /><path d="M12 11v5.5M12 7.6v.2" /></S>;
export const IconEnter = (p: P) => <S {...p}><path d="M20 5v7a3 3 0 0 1-3 3H5" /><path d="m9 11-4 4 4 4" /></S>;
export const IconBackspace = (p: P) => <S {...p}><path d="M8.5 5H20a1.5 1.5 0 0 1 1.5 1.5v11A1.5 1.5 0 0 1 20 19H8.5L2.5 12z" /><path d="m11.5 9.5 5 5M16.5 9.5l-5 5" /></S>;
export const IconCursor = (p: P) => <S {...p}><path d="M5 3.5 19 10l-6.2 1.9L10 18.5z" /></S>;
export const IconUndo = (p: P) => <S {...p}><path d="M9 14 4 9l5-5" /><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11" /></S>;
export const IconSpinner = ({ size = 16 }: P) => (
  <svg width={size} height={size} viewBox="0 0 24 24" className="spin" aria-hidden>
    <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.2" strokeWidth="2.4" />
    <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
  </svg>
);
export const IconCode = (p: P) => <S {...p}><path d="m8 7-5 5 5 5M16 7l5 5-5 5M13.5 4l-3 16" /></S>;
