"use client";

import { useEffect, useState, type ReactNode } from "react";
import { I18nextProvider } from "react-i18next";

import i18next, { initI18n, supportedLanguages } from "@/i18n/setup";

type I18nProviderProps = {
  children: ReactNode;
};

export function I18nProvider({ children }: I18nProviderProps) {
  const [ready, setReady] = useState(i18next.isInitialized);

  useEffect(() => {
    let cancelled = false;

    async function setupLanguage() {
      await initI18n();

      const savedLanguage = localStorage.getItem("preferred-language");
      if (savedLanguage && supportedLanguages.includes(savedLanguage as (typeof supportedLanguages)[number])) {
        await i18next.changeLanguage(savedLanguage);
      } else {
        const browserLanguage = navigator.language;
        const matchedLanguage = supportedLanguages.find(
          (language) => browserLanguage === language || browserLanguage.startsWith(language),
        );
        if (matchedLanguage) {
          await i18next.changeLanguage(matchedLanguage);
        }
      }

      if (!cancelled) {
        setReady(true);
      }
    }

    void setupLanguage();

    return () => {
      cancelled = true;
    };
  }, []);

  if (!ready) {
    return null;
  }

  return <I18nextProvider i18n={i18next}>{children}</I18nextProvider>;
}
