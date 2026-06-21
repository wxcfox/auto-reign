"use client";

import { Languages } from "lucide-react";

import { languageNames, useTranslation } from "@/hooks/useTranslation";

export function LanguageSwitcher() {
  const { changeLanguage, getCurrentLanguage, getSupportedLanguages, t } = useTranslation("common");
  const currentLanguage = getCurrentLanguage();
  const supported = getSupportedLanguages();

  return (
    <label className="language-switcher">
      <span className="sr-only">{t("app.language")}</span>
      <Languages aria-hidden="true" size={16} />
      <select
        aria-label={t("app.language")}
        onChange={(event) => changeLanguage(event.target.value)}
        value={currentLanguage}
      >
        {supported.map((language) => (
          <option key={language} value={language}>
            {languageNames[language] ?? language}
          </option>
        ))}
      </select>
    </label>
  );
}
