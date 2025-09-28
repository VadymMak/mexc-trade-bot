import { useSymbols } from "@/store/symbols";
import { useSSEQuotes } from "@/hooks/useSSEQuotes";
import StrategySettingsHost from "@/components/settings/StrategySettingsHost";

export default function App() {
  const symbols = useSymbols((s) => s.items.map((it) => it.symbol));
  useSSEQuotes(symbols);
  return <StrategySettingsHost />; // смонтирован один раз глобально
}
