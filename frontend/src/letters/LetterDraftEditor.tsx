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
 *
 * **Текст — в окне, а не простынёй вниз** (замечание Anthony 09.10.2026): раскрытые
 * зоны отодвигали форму сборки на экран вниз. На странице — кнопка и отметка
 * «поправлен / по умолчанию»; правка живёт в состоянии формы и закрытием окна
 * не теряется.
 */

import { Badge, Button, Group, Modal, Stack, Text, TextInput, Textarea } from '@mantine/core';
import { IconPencil } from '@tabler/icons-react';
import { useState } from 'react';

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
      '{{donor_audience}} — фраза про страну аудитории площадки (пустая, если гео донора неизвестно), ' +
      '{{donor_country}} — сама страна по-английски (гео неизвестно — письмо с ней не уйдёт), ' +
      '{{sender_name}} — имя в подписи. Ссылка стоит только в неизменяемых зонах: в переписываемой ' +
      'модель могла бы её пересказать, и сервер такой текст не примет. Цену донора не называть.',
    rewrite: 'Переписывает модель под каждого рекламодателя',
  },
};

export function LetterDraftEditor({ fallback, value, onChange, stage = 'donors' }: Props) {
  const [open, setOpen] = useState(false);
  const edited = !sameDraft(value, draftOf(fallback));

  const setZone = (name: string, text: string) =>
    onChange({ ...value, zones: { ...value.zones, [name]: text } });

  return (
    <Stack gap="sm" data-letter-draft>
      <Group gap="sm">
        <Button
          variant="subtle"
          className="press"
          aria-haspopup="dialog"
          leftSection={<IconPencil size={16} aria-hidden />}
          onClick={() => setOpen(true)}
        >
          Текст первого письма
        </Button>
        <Badge variant="light" color={edited ? 'yellow' : 'gray'}>
          {edited ? 'поправлен' : 'по умолчанию'}
        </Badge>
      </Group>

      {/* Поля — прямо на стекле окна, без вложенной подложки: поле во вложенном
          блоке — второй слой того же рецепта, фон светлел, и текст в поле тёмной
          темы намерился на 4,18 : 1 при 5,67 у поля прямо на карточке (23.09.2026). */}
      <Modal opened={open} onClose={() => setOpen(false)} title="Текст первого письма" size="lg">
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
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
              maxRows={8}
              value={value.zones[zone.name] ?? ''}
              onChange={(event) => setZone(zone.name, event.currentTarget.value)}
            />
          ))}
          <Group justify="space-between" mt="xs">
            <Button
              variant="subtle"
              className="press"
              disabled={!edited}
              onClick={() => onChange(draftOf(fallback))}
            >
              Вернуть исходный текст
            </Button>
            <Button className="press" onClick={() => setOpen(false)}>
              Готово
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
