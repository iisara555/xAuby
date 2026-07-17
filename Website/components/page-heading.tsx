export function PageHeading({ eyebrow, title, description, aside }: { eyebrow: string; title: string; description?: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <header className="page-heading">
      <div>
        <p className="page-heading-eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        {description && <p className="page-heading-description">{description}</p>}
      </div>
      {aside}
    </header>
  );
}
