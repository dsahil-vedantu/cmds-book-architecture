import { Icon, type IconName } from './Icon';

type Props = {
  icon: IconName;
  title: string;
  count?: string;
  badge?: React.ReactNode;
};

export function SectionHeader({ icon, title, count, badge }: Props) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
      <div
        style={{
          width: 26,
          height: 26,
          borderRadius: 7,
          background: 'var(--indigo-50)',
          color: 'var(--indigo-700)',
          display: 'grid',
          placeItems: 'center',
        }}
      >
        <Icon name={icon} size={14} />
      </div>
      <h3
        style={{
          fontSize: 15,
          fontWeight: 700,
          color: 'var(--ink-900)',
          margin: 0,
          letterSpacing: '-0.01em',
        }}
      >
        {title}
      </h3>
      {count && <span className="kbd" style={{ fontSize: 11 }}>{count}</span>}
      {badge}
    </div>
  );
}
