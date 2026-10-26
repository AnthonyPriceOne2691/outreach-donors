/**
 * Вкладка «Рассылка» — прогноз отправки продаж: в каком режиме уходят письма, когда уйдёт очередь,
 * сколько адресов на каком шаге, план на семь дней, факт семи прошлых и ящики с разгоном.
 *
 * **Экран своего счёта не ведёт.** Две ручки — два источника, и каждое число — функцией сервера:
 * части адресов и строки режима — `GET /sales/mailing` (решение сборки, счёт очереди пачки), срок,
 * план, факт и ящики — прогноз рассылки `GET /letters/forecast?stage=sales` (мощность ящиков с
 * разгоном, окно получателя, темп автоотправки). Одно число на двух экранах — одна функция: «в
 * очереди» — тот же счёт, что у пачки, «план» — тот же, что у недельного отчёта.
 *
 * **Блоки — в порядке вопросов человека** (`docs/UI_RULES.md`: исход, после которого действие
 * невозможно, — плашкой; главное — на виду, подробности — раскрытием): почему письма не уходят
 * вовсе — плашкой первой; режим — одной строкой на тумблер, словами сервера; когда уйдёт очередь;
 * что за очередью — адреса по частям, причины по нажатию; план по дням; факт; ящики — справкой,
 * откуда мощность плана.
 *
 * **Обе ручки — раз в минуту**, и видно, когда обновлено (старшее из двух). Отказ одной — словами в
 * её блоке, блоки другой живут; отказ очередного обновления — строкой над прежними числами.
 */

import { Group, Stack, Text } from '@mantine/core';
import type { UseQueryResult } from '@tanstack/react-query';

import { InfoHint } from '../components/InfoHint';
import { formatTime } from '../format';
import { AutoLines } from './AutoLines';
import { NoAnswer, NotRefreshed } from './MailingBlock';
import { MailingBoxes } from './MailingBoxes';
import { MailingPast, MailingPlan } from './MailingDays';
import { MailingParts } from './MailingParts';
import { MailingBlocked, MailingQueue } from './MailingQueue';
import { updatedAt, useSalesForecast, useSalesMailing } from './mailingData';
import type { SalesMailingView } from './mailingTypes';

/** Вступление одной строкой, как считается — в «i»; справа — когда обновлено. */
function Intro({ updated }: { updated: number | null }) {
  return (
    <Group justify="space-between" align="flex-start" gap="xs" px="md" wrap="wrap">
      <Group gap={4} wrap="nowrap" align="flex-start" style={{ flex: '1 1 20rem', minWidth: 0 }}>
        <Text size="sm" className="mailingIntro">
          Прогноз отправки первых писем продаж: когда уйдёт очередь, сколько писем уйдёт по дням и
          сколько адресов на каком шаге.
        </Text>
        <InfoHint name="Как считается прогноз рассылки" width={360}>
          Числа — сервера, теми же правилами, что у отправки и сборки: мощность ящиков — ступенью
          разгона и лимитами доменов и направления, окно получателя — по его часам, части адресов —
          решением сборки. Дни — по UTC, как дневной лимит ящиков.
        </InfoHint>
      </Group>
      {updated === null ? null : (
        <Text size="sm" c="dimmed" className="mailingUpdated">
          обновлено в {formatTime(new Date(updated).toISOString())} · раз в минуту
        </Text>
      )}
    </Group>
  );
}

/** Режим — строкой автоотправки и строкой автосборки сервера; отказ ручки — здесь, один раз: этот
 *  блок у ручки частей адресов первый. */
function Mode({ query }: { query: UseQueryResult<SalesMailingView> }) {
  const view = query.data;
  if (view === undefined) {
    return (
      <NoAnswer
        query={query}
        failed="Режим и адреса по гипотезам не загрузились"
        loading="Загружаем режим и адреса"
      />
    );
  }
  return (
    <Stack gap="xs" className="mailingMode">
      <NotRefreshed query={query} />
      <AutoLines autosend={view.autosend} autobuild={view.autobuild} />
    </Stack>
  );
}

export function MailingPane() {
  const mailing = useSalesMailing();
  const forecast = useSalesForecast();
  const view = forecast.data;
  return (
    <Stack gap="lg">
      <Intro updated={updatedAt(mailing, forecast)} />
      <MailingBlocked view={view} />
      <Mode query={mailing} />
      <MailingQueue query={forecast} />
      {mailing.data === undefined ? null : <MailingParts view={mailing.data} />}
      {view === undefined ? null : (
        <>
          <MailingPlan view={view} />
          <MailingPast view={view} />
          <MailingBoxes view={view} />
        </>
      )}
    </Stack>
  );
}
