export function PageHeading({ eyebrow, title, aside }: { eyebrow: string; title: string; aside?: React.ReactNode }) {
  return (
    <header className="page-heading">
      <div><p>{eyebrow}</p><h1>{title}</h1></div>
      {aside}
    </header>
  );
}
