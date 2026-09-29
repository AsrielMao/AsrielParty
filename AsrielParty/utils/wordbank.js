/**
 * 谁是卧底 · 词库读取
 *
 * 词库本身在 data/words.js（原因见那个文件开头的说明）。
 * 这里只负责：随机抽一组词，并随机决定哪个词归平民、哪个归卧底。
 */

const WORDS = require('../data/words')

/** 词库里有多少组词 */
function size() {
  return WORDS.length
}

/**
 * 随机抽一组词。
 * @returns {{ index: Number, civilian: String, undercover: String }}
 */
function draw() {
  if (!WORDS.length) {
    throw new Error('词库是空的，往 data/words.js 里加几组词')
  }

  const index = Math.floor(Math.random() * WORDS.length)
  const pair = WORDS[index]
  const words = (pair && pair.words) || []

  if (words.length < 2) {
    throw new Error('词库第 ' + (index + 1) + ' 组词的格式不对，需要两个词')
  }

  // 同一组词，随机决定哪个当平民词 —— 换个局可能就反过来了
  const flip = Math.random() < 0.5

  return {
    index: index,
    civilian: flip ? words[1] : words[0],
    undercover: flip ? words[0] : words[1]
  }
}

/** 全部词库（调试用） */
function all() {
  return WORDS
}

module.exports = {
  size: size,
  draw: draw,
  all: all
}
