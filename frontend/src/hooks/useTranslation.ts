import { useTranslation as useI18nextTranslation } from "react-i18next";

import { supportedLanguages } from "@/i18n/setup";

export function useTranslation(namespace?: string | string[]) {
  const { t, i18n } = useI18nextTranslation(namespace);

  const changeLanguage = (language: string) => {
    if (supportedLanguages.includes(language as (typeof supportedLanguages)[number])) {
      i18n.changeLanguage(language);
      localStorage.setItem("preferred-language", language);
    }
  };

  const getCurrentLanguage = () => i18n.language;
  const getSupportedLanguages = () => [...supportedLanguages];

  return {
    t,
    changeLanguage,
    getCurrentLanguage,
    getSupportedLanguages,
    i18n,
  };
}

export const languageNames: Record<string, string> = {
  en: "English",
  "zh-CN": "简体中文",
};
