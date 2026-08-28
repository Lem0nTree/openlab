import { LoginForm } from "@/components/login-form";
import { loginDestination } from "@/lib/login-redirect";

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ created?: string; next?: string | string[] }> }) {
  const { created, next } = await searchParams;
  return <LoginForm created={created === "1"} destination={loginDestination(typeof next === "string" ? next : undefined)} />;
}
