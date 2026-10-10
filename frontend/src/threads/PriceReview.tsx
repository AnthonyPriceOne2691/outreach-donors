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
 *
 * **Цена уходит строкой, как вписана, а проверяет сервер — словами**, тем же
 * правилом, что «Указать цену» на карточке донора (`donors/PriceForm`,
 * `replies/confirmation.py`). Проверка QA 10.10.2026: «−5 EUR» ложилось ценой в
 * карточку донора, «сто евро» получало английский отказ разбора схемы, а запятую
 * экран сам менял на точку — «1,200» записывалось как 1,20. Теперь отказ сервера
 * стоит над полями, вписанное остаётся на месте, а запятую сервер не угадывает,
 * а называет, как написать.
 *
 * **«Не продаёт размещения» — с подтверждением у кнопки** (`ConfirmPopover`):
 * она стоит вплотную к «Подтвердить», а промах одним нажатием уводил домен из
 * отбора на год (проверка QA 10.10.2026).
 */

import { Alert, Badge, Button, Group, Stack, Text, TextInput } from '@mantine/core';
import { useEffect, useState } from 'react';

import type { IncomingCard } from '../api/types';
import { ConfirmPopover } from '../components/ConfirmPopover';
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
  /** Почему сервер не принял решение по этому ответу — словами; `null` — не отказывал. */
  refusal: string | null;
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

/** Вписанное — как есть, без догадок; пустое поле — «нет». */
function typed(value: string): string | null {
  const trimmed = value.trim();
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

/** Что будет после «Не продаёт размещения» — словами подтверждения у кнопки. */
const DECLINE_WORDS =
  'Отметить, что донор не продаёт размещения? Домен уйдёт из отбора на год: новые прогоны его не возьмут.';

/** Ответ, который уже не ждёт человека: заголовок, уверенность и что решено. */
function DecidedHead({ incoming, heading }: { incoming: IncomingCard; heading: string }) {
  return (
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
  );
}

function PriceHead({ incoming, heading, onClose }: PriceHeadProps & { incoming: IncomingCard }) {
  return (
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
        <DecidedHead incoming={incoming} heading={heading} />
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
  );
}

export function PriceReview({
  incoming,
  canReview,
  busy,
  onConfirm,
  onDecline,
  refusal,
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
      <PriceHead incoming={incoming} heading={heading} onClose={onClose} />

      {/* Отказ — над полями, одним абзацем, как плашка выше: уведомление в углу
          закрывало «Подтвердить» и «Не продаёт размещения» (проверка QA 10.10.2026). */}
      {refusal !== null && (
        <Alert color="red" py={6} px="sm">
          <Text span size="sm" fw={600}>
            Не подтвердили
          </Text>{' '}
          <Text span size="sm">
            — {refusal}
          </Text>
        </Alert>
      )}

      {/* Поля и решения — одним рядом, что такое белая и серая — в «i» над ним:
          пояснение под каждым полем добавляло строку, и на 1280 ряд переносился
          (09.10.2026). Поля — гибкой ширины: от подписи до прежних 104/80 px.
          С жёсткой шириной ряд вставал на 1280 впритык (641 px из 642) и
          переносился от любой мелочи — так случилось, когда у пунктов меню
          появились значки и рабочая область стала на 32 px уже (10.10.2026).
          Цифры — с цифровой клавиатурой на телефоне, как у «Указать цену». */}
      <Group gap="xs" align="flex-end">
        <TextInput
          label="Белая цена"
          inputMode="decimal"
          value={white}
          style={{ flex: '1 1 80px', maxWidth: 104 }}
          disabled={!canReview}
          onChange={(event) => setWhite(event.currentTarget.value)}
        />
        <TextInput
          label="Серая цена"
          inputMode="decimal"
          value={grey}
          style={{ flex: '1 1 80px', maxWidth: 104 }}
          disabled={!canReview}
          onChange={(event) => setGrey(event.currentTarget.value)}
        />
        <TextInput
          label="Валюта"
          value={currency}
          style={{ flex: '1 1 56px', maxWidth: 80 }}
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
              price_white: typed(white),
              price_grey: typed(grey),
              currency: typed(currency),
            })
          }
        >
          Подтвердить
        </Button>
        {/* Отдельной кнопкой, а не пустыми полями: «цены нет в письме» и
            «донор сказал, что не продаёт» — разные ответы, и второй убирает
            домен из отбора на год. */}
        <ConfirmPopover message={DECLINE_WORDS} confirm="Отметить" onConfirm={onDecline}>
          {(ask) => (
            <Button
              variant="default"
              className="press"
              px="md"
              disabled={!canReview || busy}
              onClick={ask}
            >
              Не продаёт размещения
            </Button>
          )}
        </ConfirmPopover>
      </Group>

      {!canReview && (
        <Text size="xs" c="dimmed">
          Подтверждение цены — отдельное действие, его выдаёт админ. Смотреть разбор можно всем.
        </Text>
      )}
    </Stack>
  );
}
