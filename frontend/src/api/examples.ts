/**
 * Сборка ключей по примерам сайтов (`POST /keywords/examples`, `keywords/by_examples.py`):
 * главные примеров → общая тема → тот же генератор, что у сборки по теме.
 *
 * Своим файлом, а не в общем `types.ts`: запрос и ответ нужны одному месту экрана
 * прогона — блоку «По примерам сайтов».
 */

import { request } from './client';
import type { KeywordPool } from './types';

export interface ExamplesRequest {
  /** Строки поля, как вписаны, вместе с пустыми: отказ сервера называет строку
   *  по её номеру в поле. */
  examples: string[];
  preset: string;
  country: string;
  cap: number;
}

export interface ExampleSite {
  /** Имя сайта письменностью сайта, без `www.` и пути. */
  host: string;
  /** Главная прочитана — тема стоит и на ней. */
  read: boolean;
  /** Почему не прочитана — словами сервера; пусто — прочитана. */
  reason: string;
  /** Заголовок главной: по нему видно, что именно прочитано. */
  title: string;
  /** Прочитана, но, по словам модели, не про общую тему примеров. */
  off_theme: boolean;
}

export interface ExamplesPool extends KeywordPool {
  /** Общая тема примеров — коротко, по-русски. */
  theme: string;
  /** Язык рынка → тема ниши на нём: ровно это ушло генератору. */
  topics: Record<string, string>;
  sites: ExampleSite[];
  /** Строк поля, повторивших уже взятый сайт. */
  repeats: number;
  /** Из `tokens` — на вызов о теме. */
  theme_tokens: number;
}

export function buildFromExamples(body: ExamplesRequest): Promise<ExamplesPool> {
  return request<ExamplesPool>('/keywords/examples', { method: 'POST', body });
}
