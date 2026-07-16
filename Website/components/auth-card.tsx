import Link from "next/link";

export function AuthCard({
  eyebrow,
  title,
  description,
  children,
  footer,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <main className="auth-shell">
      <section className="auth-card">
        <Link className="wordmark auth-wordmark" href="/"><span />xAuby</Link>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p className="auth-description">{description}</p>
        {children}
        {footer && <div className="auth-footer">{footer}</div>}
      </section>
    </main>
  );
}
