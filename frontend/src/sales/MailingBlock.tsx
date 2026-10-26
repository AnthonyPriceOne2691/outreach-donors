/**
 * Каркас блока вкладки «Рассылка»: заголовок с «i» и что встаёт вместо чисел — крутилка до первого
 * ответа, отказ ручки словами, «не обновилось» над прежними числами.
 *
 * **Отказ одной ручки — в её блоке, остальные живут.** Части адресов и прогноз — разные ручки:
 * отказ одной не прячет числа другой. Отказ первого ответа встаёт плашкой на месте первого блока
 * этой ручки; отказ очередного обновления — строкой над прежними числами, с временем, на которое
 * они верны: числа есть, но уже не свежие, и это видно.
 *
 * **Таблица блока — с той же кромки, что его строки** (`Cards`): на широком окне текст первой
 * колонки стоит на поле ячейки, а на телефоне строка — карточкой без полей ячеек (`phoneCards`), и
 * текст карточек уезжал на 16 px левее заголовка блока (снимок стенда 390 px, 26.10.2026).
 */

import { Alert, Box, Group, Loader, Stack, Text, Title } from '@mantine/core';
import type { UseQueryResult } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { refusalOf } from '../api/client';
import { InfoHint } from '../components/InfoHint';
import { formatTime } from '../format';

interface BlockProps {
  title: string;
  /** Как считается — в «i» у заголовка: это читают раз, а не на каждом заходе. */
  hint?: ReactNode;
  /** Класс блока — по нему его находят замер контраста и снимки стенда. */
  className: string;
  children: ReactNode;
}

export function Block({ title, hint, className, children }: BlockProps) {
  return (
    <Stack gap="sm" component="section" aria-label={title} className={className}>
      <Group gap={4} wrap="nowrap" px="md" align="center">
        <Title order={5}>{title}</Title>
        {hint === undefined ? null : (
          <InfoHint name={`Как считается «${title}»`} width={360}>
            {hint}
          </InfoHint>
        )}
      </Group>
      {children}
    </Stack>
  );
}

interface NoAnswerProps {
  query: UseQueryResult<unknown>;
  /** Заголовок отказа: что не загрузилось. */
  failed: string;
  /** Имя крутилки для программы чтения. */
  loading: string;
}

/** Ответа ещё нет: крутилка — или отказ ручки словами сервера. */
export function NoAnswer({ query, failed, loading }: NoAnswerProps) {
  if (query.error) {
    return (
      <Alert color="red" title={failed} mx="md">
        {refusalOf(query.error)}
      </Alert>
    );
  }
  return <Loader aria-label={loading} m="md" />;
}

/** Числа есть, а последнее обновление отказало: прежние стоят, и сказано, на какое время. */
export function NotRefreshed({ query }: { query: UseQueryResult<unknown> }) {
  if (!query.isRefetchError) return null;
  const at = formatTime(new Date(query.dataUpdatedAt).toISOString());
  return (
    <Alert color="yellow" py={6} px="sm" mx="md" className="mailingStale">
      <Text size="sm">
        Не обновилось: {refusalOf(query.error)}. Числа — на {at}.
      </Text>
    </Alert>
  );
}

interface CardsProps {
  /** Ширина таблицы на широком окне — у узкой таблицы колонки не растягиваются во всю панель. */
  width?: string;
  children: ReactNode;
}

/** Таблица блока: на телефоне — поле, как у строк блока (карточки без полей ячеек), на широком
 *  окне — без него (поле у ячеек); узкой таблице — своя ширина. Порог — тот же, что у карточек
 *  (`phoneCards`, 48em). */
export function Cards({ width, children }: CardsProps) {
  return (
    <Box px={{ base: 'md', sm: 0 }} {...(width === undefined ? {} : { maw: { sm: width } })}>
      {children}
    </Box>
  );
}
