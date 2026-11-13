import { Brand } from "@/components/brand";

export default function AuthLayout({ children }: LayoutProps<"/">) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 px-4 py-10">
      <Brand className="text-lg" />
      <main className="w-full max-w-sm">{children}</main>
      <p className="max-w-sm text-center text-xs leading-relaxed text-muted-foreground">
        Trading carries a risk of loss. This service executes your instructions; it does not give advice or promise
        returns.
      </p>
    </div>
  );
}
