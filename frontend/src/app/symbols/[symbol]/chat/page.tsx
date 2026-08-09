import SymbolChat from "@/components/SymbolChat";

export const dynamic = "force-dynamic";

export default async function ChatPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  return <SymbolChat symbol={symbol} />;
}
