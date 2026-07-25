import { create } from "zustand";
import type {
  DayTradingAlert,
  DayTradingPerformance,
  DayTradingPosition,
  DayTradingSettings,
  DayTradingSignal,
  DayTradingTrade,
  EmergencyEvent,
  MarketRegime,
  SignalSelectionPayload,
  StreamConnection,
} from "@/lib/day-trading-types";

interface DayTradingState {
  regime: MarketRegime | null;
  signals: DayTradingSignal[];
  candidates: DayTradingSignal[];
  positions: DayTradingPosition[];
  alerts: DayTradingAlert[];
  trades: DayTradingTrade[];
  performance: DayTradingPerformance | null;
  settings: DayTradingSettings | null;
  connection: StreamConnection;
  emergency: EmergencyEvent | null;
  eventIds: string[];
  setInitial: (values: Partial<DayTradingState>) => void;
  setConnection: (value: StreamConnection) => void;
  handleEvent: (type: string, id: string, payload: unknown) => void;
  dismissEmergency: () => void;
}

export const useDayTradingStore = create<DayTradingState>((set, get) => ({
  regime: null,
  signals: [],
  candidates: [],
  positions: [],
  alerts: [],
  trades: [],
  performance: null,
  settings: null,
  connection: "connecting",
  emergency: null,
  eventIds: [],
  setInitial: (values) => set(values),
  setConnection: (connection) => set({ connection }),
  dismissEmergency: () => set({ emergency: null }),
  handleEvent: (type, id, payload) => {
    if (id && get().eventIds.includes(id)) return;
    const eventIds = id ? [...get().eventIds.slice(-199), id] : get().eventIds;
    if (type === "emergency_exit") {
      const emergency = payload as EmergencyEvent;
      set({ emergency, eventIds, connection: "connected" });
      return;
    }
    if (type === "market_update" || type === "data_delay" || type === "data_disconnected") {
      set({ regime: payload as MarketRegime, eventIds, connection: type === "data_disconnected" ? "disconnected" : "connected" });
      return;
    }
    if (type === "new_signal" || type === "signal_update" || type === "quote_update") {
      if (Array.isArray(payload)) {
        set({ signals: payload as DayTradingSignal[], candidates: payload as DayTradingSignal[], eventIds, connection: "connected" });
      } else {
        const selection = payload as SignalSelectionPayload;
        set({
          signals: selection.recommended ?? [],
          candidates: selection.candidates ?? [],
          eventIds,
          connection: "connected",
        });
      }
      return;
    }
    if (type === "position_update") {
      const position = payload as DayTradingPosition;
      const positions = get().positions.some((item) => item.id === position.id)
        ? get().positions.map((item) => item.id === position.id ? position : item)
        : [position, ...get().positions];
      set({ positions, eventIds, connection: "connected" });
      return;
    }
    if (type === "exit_warning") {
      const event = payload as EmergencyEvent;
      const alert: DayTradingAlert = {
        id: Date.now(), level: "important", type, title: event.title, message: event.message,
        action: event.action, reason: event.reason, price: event.price, createdAt: new Date().toISOString(),
      };
      set({ alerts: [alert, ...get().alerts], eventIds });
      return;
    }
    set({ eventIds });
  },
}));
