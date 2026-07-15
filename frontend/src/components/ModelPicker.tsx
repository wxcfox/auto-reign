import { ChevronDown } from "lucide-react";

import type { ModelProvider, ModelRef } from "@/lib/types";

type ModelPickerProps = {
  agentDefault: ModelRef | null;
  disabled?: boolean;
  labels: {
    agentDefault: string;
    followAgentDefault: string;
    listbox: string;
    modelUnavailable: string;
    noProviders: string;
    selectModel: string;
  };
  onOpenChange: (open: boolean) => void;
  onSelect: (value: ModelRef | null) => void;
  open: boolean;
  providers: ModelProvider[];
  selected: ModelRef | null;
};

export function ModelPicker({
  agentDefault,
  disabled = false,
  labels,
  onOpenChange,
  onSelect,
  open,
  providers,
  selected,
}: ModelPickerProps) {
  const resolvedDefault = agentDefault
    ? `${agentDefault.provider} / ${agentDefault.model}`
    : labels.modelUnavailable;
  const defaultHelper = (labels.agentDefault ?? "{{model}}").replace(
    "{{model}}",
    resolvedDefault,
  );
  const followLabel = labels.followAgentDefault ?? labels.modelUnavailable;

  const select = (value: ModelRef | null) => {
    if (disabled) {
      return;
    }
    onSelect(value);
    onOpenChange(false);
  };

  return (
    <div className="model-picker" data-open={open}>
      <button
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label={labels.selectModel}
        className="model-picker-button"
        disabled={disabled}
        onClick={() => onOpenChange(!open)}
        type="button"
      >
        <span>{selected?.model ?? followLabel}</span>
        <ChevronDown aria-hidden="true" size={14} />
      </button>
      {open ? (
        <div className="model-picker-menu" role="listbox" aria-label={labels.listbox}>
          <div className="model-picker-group">
            <button
              aria-selected={selected == null}
              data-active={selected == null}
              disabled={disabled}
              onClick={() => select(null)}
              role="option"
              type="button"
            >
              <span>{followLabel}</span>
              <small>{defaultHelper}</small>
            </button>
          </div>
          {providers.length === 0 ? (
            <span className="model-picker-empty">{labels.noProviders}</span>
          ) : null}
          {providers.map((provider) => (
            <div className="model-picker-group" key={provider.provider}>
              <p>{provider.provider}</p>
              {provider.models.map((model) => {
                const active = provider.provider === selected?.provider && model === selected.model;
                return (
                  <button
                    aria-selected={active}
                    data-active={active}
                    disabled={disabled}
                    key={`${provider.provider}-${model}`}
                    onClick={() => select({ provider: provider.provider, model })}
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
