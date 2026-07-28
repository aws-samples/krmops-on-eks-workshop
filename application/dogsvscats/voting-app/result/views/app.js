var socket = io.connect();

var bg1 = document.getElementById('background-stats-1');
var bg2 = document.getElementById('background-stats-2');
var catsPercent = document.getElementById('cats-percent');
var dogsPercent = document.getElementById('dogs-percent');
var result = document.getElementById('result');

function formatPercent(value) {
  return value.toFixed(1) + '%';
}

function resultText(total) {
  if (total == 0) {
    return 'No votes yet';
  } else if (total == 1) {
    return total + ' vote';
  }
  return total + ' votes';
}

var updateScores = function(){
  socket.on('scores', function (json) {
     var data = JSON.parse(json);
     var a = parseInt(data.a || 0);
     var b = parseInt(data.b || 0);

     var percentages = getPercentages(a, b);

     bg1.style.width = percentages.a + "%";
     bg2.style.width = percentages.b + "%";

     catsPercent.textContent = formatPercent(percentages.a);
     dogsPercent.textContent = formatPercent(percentages.b);
     result.textContent = resultText(a + b);
  });
};

var init = function(){
  document.body.style.opacity=1;
  updateScores();
};

socket.on('message',function(data){
  init();
});

function getPercentages(a, b) {
  var result = {};

  if (a + b > 0) {
    result.a = Math.round(a / (a + b) * 100);
    result.b = 100 - result.a;
  } else {
    result.a = result.b = 50;
  }

  return result;
}
