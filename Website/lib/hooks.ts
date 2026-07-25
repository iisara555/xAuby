"use client";

import useSWR from "swr";
import { api, Bot, Catalog, RuntimeSnapshot, TelegramPreferences, TradingProfile, User } from "./api";

const visibleRefresh = (milliseconds: number) =>
  typeof document === "undefined" || document.visibilityState === "visible" ? milliseconds : 0;

export function useMe() {
  return useSWR<User>("/api/v1/me", api, {
    revalidateOnFocus: true,
    shouldRetryOnError: false,
  });
}

export function useBot() {
  return useSWR<Bot>("/api/v1/bot", api, {
    refreshInterval: () => visibleRefresh(5000),
    revalidateOnReconnect: true,
  });
}

export function useSnapshot() {
  return useSWR<RuntimeSnapshot>("/api/v1/runtime/snapshot", api, {
    refreshInterval: () => visibleRefresh(5000),
    revalidateOnFocus: true,
    revalidateOnReconnect: true,
  });
}

export function useCatalog() {
  return useSWR<Catalog>("/api/v1/catalog", api, { revalidateOnFocus: false });
}

export function useProfile() {
  return useSWR<TradingProfile>("/api/v1/profile", api, { revalidateOnFocus: false });
}

export function useTelegramPreferences() {
  return useSWR<TelegramPreferences>("/api/v1/telegram/preferences", api, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  });
}
