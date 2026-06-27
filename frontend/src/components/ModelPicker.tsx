import { ChevronDown } from "lucide-react";

import type { ModelProvider, ProviderName } from "@/lib/types";

type ModelPickerProps = {
  disabled?: boolean;
  labels: {
    listbox: string;
    modelUnavailable: string;
    noProviders: string;
    selectModel: string;
  };
  onOpenChange: (open: boolean) => void;
  onSelect: (provider: ProviderName, model: string) => void;
  open: boolean;
  providers: ModelProvider[];
  selectedModel: string;
  selectedProvider: ProviderName;
};

export function ModelPicker({
  disabled = false,
  labels,
  onOpenChange,
  onSelect,
  open,
  providers,
  selectedModel,
  selectedProvider,
}: ModelPickerProps) {
  return (
    <div className="model-picker" data-open={open}>
      <button
        aria-expanded={open}
        aria-label={labels.selectModel}
        className="model-picker-button"
        disabled={disabled || providers.length === 0}
        onClick={() => onOpenChange(!open)}
        type="button"
      >
        <span>{selectedModel || labels.modelUnavailable}</span>
        <ChevronDown aria-hidden="true" size={14} />
      </button>
      {open ? (
        <div className="model-picker-menu" role="listbox" aria-label={labels.listbox}>
          {providers.length === 0 ? (
            <span className="model-picker-empty">{labels.noProviders}</span>
          ) : null}
          {providers.map((provider) => (
            <div className="model-picker-group" key={provider.provider}>
              <p>{provider.provider}</p>
              {provider.models.map((model) => {
                const active = provider.provider === selectedProvider && model === selectedModel;
                return (
                  <button
                    aria-selected={active}
                    data-active={active}
                    key={`${provider.provider}-${model}`}
                    onClick={() => onSelect(provider.provider, model)}
                    role="option"
                    type="button"
                  >
                    {model}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
