/**
 * Окно правки лида: имя, должность, компания, язык письма, страна и пояс.
 *
 * **Зачем.** Имя лида задавал только файл: лиду из «пишите другому» без имени и лиду без
 * языка письмо не уходило, а новая загрузка их не чинит — очистка отсечёт её как дубль.
 *
 * **Правила полей — сервера**, те же, что у загрузки базы: язык — кодом основой (`ru-RU` →
 * `ru`), страна — кодом или названием, пояс — именем из базы поясов. Его отказ встаёт над
 * формой словами, введённое остаётся на месте (`SaveRefusal`). Пустое поле — «не задано»:
 * пустой пояс окно отправки берёт по стране.
 *
 * **Письмо в очереди после правки собирается заново** — сервер называет это в ответе
 * (`rebuild`), уведомление говорит словами; до пересборки отправка его не пустит.
 *
 * **Без права отправки окно — смотреть** (`WriteRight`): имя и компания уходят в письмо.
 *
 * **В окне одна прокрутка** (`OneScrollForm`): шесть полей с пояснениями выше окна на низком
 * экране — на 375 × 667 и 844 × 390 окно прокручивалось само, и «Сохранить» стояла под краем.
 * Теперь заголовок и кнопки стоят, прокручиваются поля.
 */

import { Modal, Select, SimpleGrid, TextInput } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { changeLead } from '../api/salesLeads';
import { CHAIN_LANGUAGES } from '../api/salesLabels';
import type { LeadChanged, LeadPatchBody } from '../api/salesLeads';
import type { ChainLanguage, LeadCard } from '../api/salesTypes';
import { dropdownBelow } from '../theme';
import { OneScrollForm } from './OneScrollForm';
import { SaveActions } from './SaveActions';
import { SaveRefusal } from './SaveRefusal';
import { WriteRight } from './WriteRight';

/** «Не задан» — своё слово: у Mantine пустое значение неотличимо от «ничего не выбрано». */
const NONE = 'none';

interface Draft {
  name: string;
  position: string;
  company: string;
  language: string | null;
  country: string;
  timezone: string;
}

function draftOf(lead: LeadCard): Draft {
  return {
    name: lead.name ?? '',
    position: lead.position ?? '',
    company: lead.company ?? '',
    language: lead.language,
    country: lead.country ?? '',
    timezone: lead.timezone ?? '',
  };
}

/** Что уходит на сервер: пустое — `null`, «не задано». */
function bodyOf(draft: Draft): LeadPatchBody {
  const text = (value: string) => (value.trim() === '' ? null : value);
  return {
    name: text(draft.name),
    position: text(draft.position),
    company: text(draft.company),
    language: draft.language,
    country: text(draft.country),
    timezone: text(draft.timezone),
  };
}

function changed(draft: Draft, lead: LeadCard): boolean {
  const before = bodyOf(draftOf(lead));
  const after = bodyOf(draft);
  return (Object.keys(after) as (keyof LeadPatchBody)[]).some(
    (field) => (after[field] ?? null) !== (before[field] ?? null),
  );
}

/** Языки цепочек — и язык лида, если он другой: он виден, а не пропадает из поля. */
function languageChoices(current: string | null) {
  const known = Object.keys(CHAIN_LANGUAGES) as ChainLanguage[];
  const choices = [
    { value: NONE, label: 'не задан' },
    ...known.map((code) => ({
      value: code,
      label: `${CHAIN_LANGUAGES[code].toLowerCase()} (${code})`,
    })),
  ];
  const other = current !== null && !known.some((code) => code === current);
  return other ? [...choices, { value: current, label: `${current} — цепочки нет` }] : choices;
}

interface Props {
  lead: LeadCard;
  /** Право записи (`WriteRight`); без него окно — смотреть. */
  mayWrite: boolean;
  onClose: () => void;
  onSaved: (saved: LeadChanged) => void;
}

export function LeadEditModal({ lead, mayWrite, onClose, onSaved }: Props) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(lead));
  const save = useMutation({
    mutationFn: (body: LeadPatchBody) => changeLead(lead.id, body),
    onSuccess: onSaved,
  });
  const ready = mayWrite && changed(draft, lead);
  const edit = (patch: Partial<Draft>) => setDraft((current) => ({ ...current, ...patch }));
  const disabled = !mayWrite;

  return (
    <Modal
      opened
      onClose={onClose}
      size="lg"
      title={`Лид ${lead.email}`}
      classNames={{ content: 'oneScroll' }}
    >
      <OneScrollForm
        onSubmit={(event) => {
          event.preventDefault();
          if (ready) save.mutate(bodyOf(draft));
        }}
        top={save.error !== null && <SaveRefusal error={save.error} />}
        footer={
          <>
            <SaveActions ready={ready} busy={save.isPending} onCancel={onClose} />
            {disabled && <WriteRight what="Правит лида" />}
          </>
        }
      >
        <SimpleGrid
          cols={{ base: 1, sm: 2 }}
          spacing="md"
          verticalSpacing="sm"
          className="fieldRow"
        >
          <TextInput
            label="Имя"
            description="Встаёт в письмо на место {{name}}."
            value={draft.name}
            disabled={disabled}
            onChange={(event) => edit({ name: event.currentTarget.value })}
          />
          <TextInput
            label="Должность"
            description="Видна в строке лида рядом с именем."
            value={draft.position}
            disabled={disabled}
            onChange={(event) => edit({ position: event.currentTarget.value })}
          />
          <TextInput
            label="Компания"
            description="Встаёт в письмо на место {{company}}."
            value={draft.company}
            disabled={disabled}
            onChange={(event) => edit({ company: event.currentTarget.value })}
          />
          <Select
            label="Язык письма"
            description="Без языка письмо не уйдёт."
            data={languageChoices(lead.language)}
            value={draft.language ?? NONE}
            allowDeselect={false}
            disabled={disabled}
            comboboxProps={dropdownBelow}
            onChange={(picked) =>
              edit({ language: picked === null || picked === NONE ? null : picked })
            }
          />
          <TextInput
            label="Страна"
            description="Код или название: de, Germany, Германия."
            value={draft.country}
            disabled={disabled}
            onChange={(event) => edit({ country: event.currentTarget.value })}
          />
          <TextInput
            label="Часовой пояс"
            description="Из базы поясов: Europe/Berlin. Пусто — пояс страны."
            placeholder="по стране"
            value={draft.timezone}
            disabled={disabled}
            onChange={(event) => edit({ timezone: event.currentTarget.value })}
          />
        </SimpleGrid>
      </OneScrollForm>
    </Modal>
  );
}
