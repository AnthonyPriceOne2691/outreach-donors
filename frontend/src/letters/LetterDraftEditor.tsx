/**
 * Текст первого письма перед созданием рассылки — правка по зонам.
 *
 * **Правится содержимое, а не устройство.** Набор зон задан требованиями:
 * здесь нельзя ни добавить абзац, ни сделать условия переписываемыми.
 * У каждой зоны сказано, что с ней будет, — «переписывает модель» или
 * «уходит как есть»: иначе человек поправит приветствие и удивится,
 * что в письмах его правки не видно.
 *
 * **Проверяет сервер.** Разбор шаблона, подпись, достижимость коридора,
 * метрики — всё это делает тот же код, что разбирает шаблон из файла.
 * Второй набор правил здесь разошёлся бы с ним при первой правке.
 *
 * **Нетронутый текст не отправляется.** Тогда у новой рассылки — текст
 * по умолчанию, у найденной — её собственный, и одноимённая рассылка
 * дополняется, а не упирается в «у неё уже свой текст».
 */

import { Badge, Button, Group, Stack, Text, TextInput, Textarea } from '@mantine/core';
import { IconChevronDown } from '@tabler/icons-react';
import { useId, useState } from 'react';

import type { LetterDraft, LetterDraftView, LetterStage } from '../api/types';

export function draftOf(view: LetterDraftView): LetterDraft {
  return {
    subject: view.subject,
    zones: Object.fromEntries(view.zones.map((zone) => [zone.name, zone.text])),
  };
}

export function sameDraft(one: LetterDraft, other: LetterDraft): boolean {
  if (one.subject.trim() !== other.subject.trim()) return false;
  return Object.entries(other.zones).every(
    ([name, text]) => (one.zones[name] ?? '').trim() === text.trim(),
  );
}

interface Props {
  fallback: LetterDraftView;
  value: LetterDraft;
  onChange: (next: LetterDraft) => void;
  /** От этапа — подстановки и то, под кого модель переписывает зоны. */
  stage?: LetterStage;
}

/** Что можно подставить в текст и что нельзя трогать — по этапу. */
const HINTS: Record<LetterStage, { placeholders: string; rewrite: string }> = {
  donors: {
    placeholders:
      '{{host}} — сайт донора, {{sender_name}} — имя в подписи. Пункты списка вопросов ' +
      'модель переписывает, но потерять не может: письмо с потерянным пунктом уходит с исходной ' +
      'формулировкой.',
    rewrite: 'Переписывает модель под каждого донора',
  },
  advertisers: {
    placeholders:
      '{{donor_host}} — площадка, где нашли ссылку, {{page_url}} — страница, {{anchor}} — анкор, ' +
      '{{sender_name}} — имя в подписи. Ссылка стоит только в неизменяемых зонах: в переписываемой ' +
      'модель могла бы её пересказать, и сервер такой текст не примет. Цену донора не называть.',
    rewrite: 'Переписывает модель под каждого рекламодателя',
  },
};

export function LetterDraftEditor({ fallback, value, onChange, stage = 'donors' }: Props) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  const edited = !sameDraft(value, draftOf(fallback));

  const setZone = (name: string, text: string) =>
    onChange({ ...value, zones: { ...value.zones, [name]: text } });

  return (
    // Без подложки `glassQuiet`: поле на стекле само полупрозрачное, и поле
    // во вложенном блоке — второй слой того же рецепта. Фон светлел, и текст
    // в поле темной темы намерился на 4,18 : 1 при 5,67 у такого же поля
    // формы, лежащего прямо на карточке (замер 23.09.2026).
    <Stack gap="sm" data-letter-draft>
      <Group gap="sm">
        {/* `aria-expanded` и `aria-controls`: без них тест не отличит
            раскрытое от свёрнутого, а программа чтения — тоже. */}
        <Button
          variant="subtle"
          className="press"
          aria-expanded={open}
          aria-controls={bodyId}
          rightSection={
            <IconChevronDown
              size={16}
              style={{
                transform: open ? 'rotate(180deg)' : 'none',
                transition: 'transform 200ms cubic-bezier(0.32, 0.72, 0, 1)',
              }}
            />
          }
          onClick={() => setOpen((was) => !was)}
        >
          Текст первого письма
        </Button>
        <Badge variant="light" color={edited ? 'yellow' : 'gray'}>
          {edited ? 'поправлен' : 'по умолчанию'}
        </Badge>
      </Group>

      {/* Свёрнутое не отрисовывается, а не прячется атрибутом: `hidden`
          у компонентов Mantine перебивается их собственным `display`. */}
      {open ? (
        <Stack gap="sm" id={bodyId}>
          <Text size="sm" c="dimmed" maw={680}>
            Текст закрепляется за рассылкой при её создании и дальше не меняется. Подстановки:{' '}
            {HINTS[stage].placeholders}
          </Text>
          <TextInput
            label="Тема"
            value={value.subject}
            onChange={(event) => onChange({ ...value, subject: event.currentTarget.value })}
          />
          {fallback.zones.map((zone) => (
            <Textarea
              key={zone.name}
              label={zone.title}
              description={
                zone.kind === 'rewrite' ? HINTS[stage].rewrite : 'Уходит как есть, модель не видит'
              }
              autosize
              minRows={1}
              maxRows={12}
              value={value.zones[zone.name] ?? ''}
              onChange={(event) => setZone(zone.name, event.currentTarget.value)}
            />
          ))}
          <Group>
            <Button
              variant="subtle"
              className="press"
              disabled={!edited}
              onClick={() => onChange(draftOf(fallback))}
            >
              Вернуть исходный текст
            </Button>
          </Group>
        </Stack>
      ) : null}
    </Stack>
  );
}
