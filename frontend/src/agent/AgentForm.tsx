/**
 * Поля настроек агента одного этапа. Отказ — под полем и до нажатия
 * (`agentDraft.ts`), правка закрыта без права «настройки».
 */

import { Alert, Button, Group, NumberInput, Stack, Switch, Textarea } from '@mantine/core';

import { SaveVersionButton } from '../components/SaveVersionButton';
import { LIMITS } from './agentDraft';
import type { AgentDraft, DraftField } from './agentDraft';

type TextField = Exclude<DraftField, 'price'>;

interface TextSpec {
  field: TextField;
  label: string;
  description?: string;
  minRows: number;
  maxLength?: number;
}

const GOAL: TextSpec = {
  field: 'goal',
  label: 'Цель разговора',
  description: 'Чего добиваемся — словами, как поставили бы задачу менеджеру',
  minRows: 2,
  maxLength: LIMITS.goal,
};
const TONE: TextSpec = { field: 'tone', label: 'Тон', minRows: 1, maxLength: LIMITS.tone };
const POINTS: TextSpec = {
  field: 'points',
  label: 'Доводы и вопросы',
  description: 'По одному на строку, в том порядке, в каком агент их ведёт',
  minRows: 3,
};
const STOP_TOPICS: TextSpec = {
  field: 'stopTopics',
  label: 'Темы — человеку',
  description: 'По одной на строку: на них агент не отвечает сам, а отдаёт разговор человеку',
  minRows: 2,
};

interface FormProps {
  draft: AgentDraft;
  refusals: Partial<Record<DraftField, string>>;
  canEdit: boolean;
  /** Подпись предела на этапе: «Не дороже, $» или «Не дешевле, $». */
  priceLabel: string;
  onEdit: (change: Partial<AgentDraft>) => void;
  save: { busy: boolean; disabled: boolean; onSave: () => void };
  /** Вернуть действующие; нет — черновик с ними совпадает. */
  onRevert: (() => void) | null;
}

export function AgentForm({
  draft,
  refusals,
  canEdit,
  priceLabel,
  onEdit,
  save,
  onRevert,
}: FormProps) {
  const text = (spec: TextSpec) => (
    <Textarea
      label={spec.label}
      description={spec.description}
      autosize
      minRows={spec.minRows}
      maxLength={spec.maxLength}
      disabled={!canEdit}
      value={draft[spec.field]}
      error={refusals[spec.field]}
      onChange={(event) => onEdit({ [spec.field]: event.currentTarget.value })}
    />
  );
  return (
    <Stack gap="md">
      <Switch
        label="Агент готовит черновики ответов на этом этапе"
        checked={draft.enabled}
        disabled={!canEdit}
        onChange={(event) => onEdit({ enabled: event.currentTarget.checked })}
      />
      {text(GOAL)}
      {text(TONE)}
      {text(POINTS)}
      <NumberInput
        label={priceLabel}
        description="Пусто — предела нет, и цену агент не обещает: её называет человек"
        w="18rem"
        min={0}
        max={LIMITS.price}
        clampBehavior="none"
        decimalScale={2}
        allowNegative={false}
        thousandSeparator=" "
        disabled={!canEdit}
        value={draft.price}
        error={refusals.price}
        onChange={(value) => onEdit({ price: typeof value === 'number' ? value : '' })}
      />
      {text(STOP_TOPICS)}

      {!canEdit && (
        <Alert color="yellow" title="Править настройки агента не разрешено">
          Смотреть текущие и историю можно, правка выдана отдельным правом.
        </Alert>
      )}

      <Group mt="xs">
        <SaveVersionButton {...save} />
        {onRevert !== null && (
          <Button variant="subtle" className="press" onClick={onRevert}>
            Вернуть действующие
          </Button>
        )}
      </Group>
    </Stack>
  );
}
