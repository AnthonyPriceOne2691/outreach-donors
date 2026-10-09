/**
 * Разбор цены под текстом ответа: что увидела модель и что решил человек.
 *
 * **Поля стоят рядом с исходным текстом, а не вместо него.** Проверить
 * спорный разбор можно только сравнением: человек читает письмо и видит,
 * откуда взялось число. Карточка, показывающая одно число вместо письма,
 * проверке не поддаётся вовсе.
 *
 * **Уверенность модели показана, а не спрятана.** «Уверенность 40%» —
 * это и есть объяснение, почему цена не попала в базу сама.
 *
 * **Подтверждение человека сильнее модели.** Нажав «подтвердить», он
 * кладёт цену в карточку донора независимо от того, что насчитала
 * модель. Обратного действия нет намеренно: передумав, он правит цифры
 * и подтверждает снова — это честнее, чем «отменить подтверждение»,
 * после которого непонятно, какое число считается верным.
 *
 * **Причина — от сервера, если она не «уверенности не хватило».** Автоответ
 * с суммой модель не разбирала вовсе: объяснение про уверенность было бы
 * неправдой, и человек искал бы разбор, которого нет.
 *
 * **Форма — в две строки: причина и ряд полей с кнопками** (аудит экранов
 * 09.10.2026, второй круг). Уверенность — в той же строке, что «подтверждает
 * человек», подсказки полей — по слову: разбор в 317 px вместе с шапкой сжимал
 * ленту переписки до 192 px из 792.
 */

import { Alert, Badge, Button, Group, Stack, Text, TextInput } from '@mantine/core';
import { useEffect, useState } from 'react';

import type { IncomingCard } from '../api/types';
import { InfoHint } from '../components/InfoHint';

export interface PriceReviewProps {
  incoming: IncomingCard;
  canReview: boolean;
  busy: boolean;
  onConfirm: (values: {
    price_white: string | null;
    price_grey: string | null;
    currency: string | null;
  }) => void;
  /** Донор ответил «не продаём размещения». Для гест-постинга это ответ на
   *  главный вопрос письма: он уходит в отбор, и домен выходит из прогонов. */
  onDecline: () => void;
}

/** Почему цену подтверждает человек и что сделать — одной строкой. */
function reviewWords(incoming: IncomingCard): string {
  if (incoming.review_reason) {
    return `— причина: ${incoming.review_reason}. Впишите цену из письма.`;
  }
  const sure =
    incoming.confidence === null
      ? ''
      : ` уверенность разбора ${(incoming.confidence * 100).toFixed(0)}%:`;
  return `—${sure} сверьте числа с письмом.`;
}

function clean(value: string): string | null {
  const trimmed = value.trim().replace(',', '.');
  return trimmed === '' ? null : trimmed;
}

/** Шапка формы: что разбираем и можно ли свернуть (`ReplyDecision`). */
export interface PriceHeadProps {
  /** «Разбор цены · ответ …, дата» — когда ответ уже не ждёт человека. */
  heading: string;
  /** Открыт кнопкой в пузыре — можно свернуть; `null` — ответ ждёт решения. */
  onClose: (() => void) | null;
}

/** Белая и серая — слова агентства: без пояснения новичок их не различит. */
const PRICE_WORDS =
  'Белая — с пометкой «партнёрский материал», серая — без пометки. Валюта — как назвал донор.';

export function PriceReview({
  incoming,
  canReview,
  busy,
  onConfirm,
  onDecline,
  heading,
  onClose,
}: PriceReviewProps & PriceHeadProps) {
  const [white, setWhite] = useState(incoming.price_white ?? '');
  const [grey, setGrey] = useState(incoming.price_grey ?? '');
  const [currency, setCurrency] = useState(incoming.currency ?? '');

  // Смена ответа сбрасывает поля: иначе цена одного донора уехала бы
  // в карточку другого.
  useEffect(() => {
    setWhite(incoming.price_white ?? '');
    setGrey(incoming.price_grey ?? '');
    setCurrency(incoming.currency ?? '');
  }, [incoming.id, incoming.price_white, incoming.price_grey, incoming.currency]);

  return (
    <Stack gap="xs">
      <Group gap="sm" justify="space-between" wrap="nowrap">
        {/* Ждёт человека — плашка и есть заголовок: что случилось и что сделать.
            Одним абзацем: рядом со списком переписка — колонка высотой окна. */}
        {incoming.needs_review ? (
          <Alert color="yellow" py={6} px="sm" style={{ flex: 1 }}>
            <Text span size="sm" fw={600}>
              Цену подтверждает человек
            </Text>{' '}
            <Text span size="sm">
              {reviewWords(incoming)}
            </Text>
          </Alert>
        ) : (
          <Group gap="sm">
            <Text size="sm" fw={600}>
              {heading}
            </Text>
            {incoming.confidence !== null && (
              <Text size="xs" c="dimmed">
                уверенность разбора {(incoming.confidence * 100).toFixed(0)}%
              </Text>
            )}
            {incoming.reviewed_by !== null && (
              <Badge variant="light" color="green">
                подтвердил {incoming.reviewed_by}
              </Badge>
            )}
            {incoming.placement === 'declines' && (
              <Badge variant="light" color="gray">
                донор: размещений не продаёт
              </Badge>
            )}
          </Group>
        )}
        <Group gap={4} wrap="nowrap">
          <InfoHint name="Белая и серая цена" width={280}>
            {PRICE_WORDS}
          </InfoHint>
          {onClose !== null && (
            <Button variant="subtle" size="compact-xs" onClick={onClose}>
              Свернуть
            </Button>
          )}
        </Group>
      </Group>

      {/* Поля и решения — одним рядом, что такое белая и серая — в «i» над ним:
          пояснение под каждым полем добавляло строку, и на 1280 ряд переносился
          (09.10.2026). */}
      <Group gap="xs" align="flex-end">
        <TextInput
          label="Белая цена"
          value={white}
          w={96}
          disabled={!canReview}
          onChange={(event) => setWhite(event.currentTarget.value)}
        />
        <TextInput
          label="Серая цена"
          value={grey}
          w={96}
          disabled={!canReview}
          onChange={(event) => setGrey(event.currentTarget.value)}
        />
        <TextInput
          label="Валюта"
          value={currency}
          w={72}
          disabled={!canReview}
          onChange={(event) => setCurrency(event.currentTarget.value)}
        />
        <Button
          color="lagoon"
          className="press"
          px="md"
          loading={busy}
          disabled={!canReview}
          onClick={() =>
            onConfirm({
              price_white: clean(white),
              price_grey: clean(grey),
              currency: clean(currency),
            })
          }
        >
          Подтвердить
        </Button>
        {/* Отдельной кнопкой, а не пустыми полями: «цены нет в письме» и
            «донор сказал, что не продаёт» — разные ответы, и второй убирает
            домен из отбора на год. */}
        <Button
          variant="default"
          className="press"
          px="md"
          disabled={!canReview || busy}
          onClick={onDecline}
        >
          Не продаёт размещения
        </Button>
      </Group>

      {!canReview && (
        <Text size="xs" c="dimmed">
          Подтверждение цены — отдельное действие, его выдаёт админ. Смотреть разбор можно всем.
        </Text>
      )}
    </Stack>
  );
}
