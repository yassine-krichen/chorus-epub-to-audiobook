import { SidebarTrigger } from "@/components/ui/sidebar";
import { UserMenu } from "@/components/layout/user-menu";

export function AppTopbar() {
  return (
    <header className="flex h-14 items-center justify-between border-b px-4">
      <SidebarTrigger />
      <UserMenu />
    </header>
  );
}
