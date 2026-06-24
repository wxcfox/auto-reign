import { useTranslation as useI18nextTranslation } from "react-i18next";

import { supportedLanguages } from "@/i18n/setup";

export function useTranslation(namespace?: string | string[]) {
  const { t, i18n } = useI18nextTranslation(namespace);

  const changeLanguage = (language: string) => {
    if (supportedLanguages.includes(language as (typeof supportedLanguages)[number])) {
      i18n.changeLanguage(language);
      try {
        window.localStorage?.setItem("preferred-language", language);
      } catch {
        // Language switching should still work when storage is unavailable.
      }
    }
  };

  const getCurrentLanguage = () => i18n.language;
  return {
    t,
    changeLanguage,
    getCurrentLanguage,
    i18n,
  };
}
